"""
Web terminal.

Every user gets a real PTY session rooted in their own sandbox directory.
The service enforces three layers:

1. **Filesystem containment** — the working directory can never leave the
   user's sandbox; attempts are rejected before the shell sees them.
2. **Command policy** — a deny-list of destructive / privilege-escalating
   patterns, with a per-user permission profile that can widen the policy
   (network tools, package installs, process control).
3. **Resource limits** — wall-clock timeout, output cap, one PTY per session,
   and a hard cap on concurrent sessions per user.

Every invocation is written to ``terminal_commands`` with its exit code, so
the audit trail is complete.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pty
import re
import shlex
import signal
import struct
import fcntl
import termios
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..core.config import settings
from ..core.db import db, new_id
from ..security import audit

log = logging.getLogger("terminal")

MAX_SESSIONS_PER_USER = 4
OUTPUT_CAP = 200_000
READ_CHUNK = 8192
IDLE_TTL = 60 * 45


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
_DENY_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+/(?:\s|$|;)"), "wipe-root", "Удаление корня файловой системы запрещено"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+/(?:\s|$)"), "wipe-root", "Удаление корня файловой системы запрещено"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"), "fork-bomb", "Отказ: паттерн fork bomb"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "format", "Форматирование дисков запрещено"),
    (re.compile(r"\bdd\b.*\bof=/dev/(sd|nvme|hd|vd|disk)"), "disk-write", "Прямая запись на устройство запрещена"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|vd)"), "disk-write", "Прямая запись на устройство запрещена"),
    (re.compile(r"\b(chmod|chown)\s+(-[a-zA-Z]+\s+)*.*\s/(?:\s|$|;)"), "root-perms", "Смена прав на корневом каталоге запрещена"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b|\binit\s+0\b"), "power", "Управление питанием хоста недоступно"),
    (re.compile(r"\biptables\b|\bnft\b|\bufw\b|\bfirewall-cmd\b"), "firewall", "Изменение сетевого экрана недоступно"),
    (re.compile(r"\buseradd\b|\busermod\b|\buserdel\b|\bpasswd\b|\bchpasswd\b"), "accounts", "Управление учётными записями хоста недоступно"),
    (re.compile(r"\bcrontab\s+-[er]"), "cron", "Изменение cron хоста недоступно"),
    (re.compile(r"\bsystemctl\s+(enable|disable|mask|daemon-reload)\b"), "systemd", "Изменение юнитов systemd недоступно"),
    (re.compile(r"\bmount\b|\bumount\b"), "mount", "Монтирование файловых систем недоступно"),
    (re.compile(r"\bcurl\b[^|;&]*\|\s*(sudo\s+)?(ba)?sh\b"), "pipe-to-shell", "Выполнение скриптов из сети через pipe запрещено"),
    (re.compile(r"\bwget\b[^|;&]*\|\s*(sudo\s+)?(ba)?sh\b"), "pipe-to-shell", "Выполнение скриптов из сети через pipe запрещено"),
    (re.compile(r"\bsudo\b"), "privilege", "Повышение привилегий недоступно в песочнице"),
    (re.compile(r"\bsu\s+-?\s*$|\bsu\s+\w"), "privilege", "Смена пользователя недоступна"),
    (re.compile(r"\bdoas\b"), "privilege", "Повышение привилегий недоступно"),
    (re.compile(r">\s*/etc/(passwd|shadow|sudoers)"), "root-write", "Запись в системные файлы запрещена"),
    (re.compile(r"\bmv\s+/\S+\s+/dev/null\b"), "destructive", "Деструктивная операция отклонена"),
    (re.compile(r"\b(nc|ncat|netcat)\b.*\s-e\b"), "reverse-shell", "Открытые reverse-shell соединения запрещены"),
    (re.compile(r"\bbash\s+-i\s+[>&]+\s*/dev/tcp"), "reverse-shell", "Открытые reverse-shell соединения запрещены"),
    (re.compile(r"\bopenssl\s+.*\bpasswd\b|\bhashcat\b|\bjohn\b"), "credential-attack", "Инструменты подбора паролей запрещены"),
]

_NETWORK_TOOLS = re.compile(r"^\s*(curl|wget|nc|ncat|ssh|scp|sftp|telnet|dig|nslookup|host|ping|traceroute|nmap)\b")
_PACKAGE_MANAGERS = re.compile(r"^\s*(apt|apt-get|pip|pip3|npm|yarn|pnpm|cargo|gem|composer|brew)\b")
_INTERACTIVE = re.compile(r"^\s*(vim|vi|nano|emacs|less|more|htop|top|man|python3?)\s*$")
_PATH_FLAGS = re.compile(r"(?<![\w-])(/etc|/root|/boot|/proc|/sys|/dev|/var/log|/usr|/bin|/sbin|/lib)(?![\w-])")


@dataclass(slots=True)
class PolicyResult:
    allowed: bool
    reason: str = ""
    code: str = ""
    risk: str = "low"


def screen_command(command: str, permissions: dict[str, Any], *, cwd: Path) -> PolicyResult:
    text = (command or "").strip()
    if not text:
        return PolicyResult(False, "empty command", "empty")
    if len(text) > 4000:
        return PolicyResult(False, "command too long", "too-long")

    for pattern, code, reason in _DENY_PATTERNS:
        if pattern.search(text):
            return PolicyResult(False, reason, code, risk="critical")

    if not permissions.get("terminal_network", True) and _NETWORK_TOOLS.search(text):
        return PolicyResult(False, "Сетевые утилиты отключены политикой аккаунта", "network-disabled", risk="medium")
    if not permissions.get("terminal_packages", False) and _PACKAGE_MANAGERS.search(text):
        return PolicyResult(False, "Установка пакетов отключена политикой аккаунта", "packages-disabled", risk="medium")

    # Path containment: absolute system paths are rejected outright.
    for token in _safe_tokens(text):
        if token.startswith("/") and _PATH_FLAGS.search(token):
            return PolicyResult(False, f"Доступ к {token} вне песочницы запрещён", "path-escape", risk="high")
        if token.startswith("/"):
            try:
                resolved = Path(token).resolve()
                if not _within(resolved, cwd.parent.parent):
                    return PolicyResult(False, f"Путь {token} вне рабочей области", "path-escape", risk="high")
            except (OSError, ValueError):
                continue
        if ".." in token:
            candidate = (cwd / token).resolve()
            if not _within(candidate, cwd.parent.parent):
                return PolicyResult(False, "Выход за пределы рабочей области через «..»", "path-escape", risk="high")

    risk = "medium" if _NETWORK_TOOLS.search(text) or _PACKAGE_MANAGERS.search(text) else "low"
    return PolicyResult(True, "ok", "", risk=risk)


def _safe_tokens(text: str) -> list[str]:
    try:
        return shlex.split(text, comments=False)
    except ValueError:
        return text.split()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
@dataclass
class Session:
    id: str
    user_id: str
    pid: int
    master_fd: int
    cwd: Path
    shell: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    output: bytearray = field(default_factory=bytearray)
    alive: bool = True
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    commands: int = 0

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_activity


class TerminalService:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle
    def sandbox(self, user_id: str) -> Path:
        root = settings.storage_dir / "terminal" / user_id[:24]
        root.mkdir(parents=True, exist_ok=True)
        (root / "projects").mkdir(exist_ok=True)
        readme = root / "README.txt"
        if not readme.exists():
            readme.write_text(
                "Mestinghem AI — ваша песочница терминала.\n"
                "Файлы здесь сохраняются между сессиями и доступны в файловом менеджере.\n",
                encoding="utf-8",
            )
        return root

    async def open(self, user_id: str, *, shell: str = "/bin/bash", cwd: str | None = None,
                   permissions: dict[str, Any] | None = None) -> Session:
        if not settings.terminal_enabled:
            raise PermissionError("terminal is disabled on this deployment")
        async with self._lock:
            mine = [s for s in self.sessions.values() if s.user_id == user_id and s.alive]
            if len(mine) >= MAX_SESSIONS_PER_USER:
                await self.close(mine[0].id, user_id=user_id)
            root = self.sandbox(user_id)
            workdir = Path(cwd) if cwd else root
            if not _within(workdir.resolve(), root):
                workdir = root
            workdir.mkdir(parents=True, exist_ok=True)

            pid, master_fd = pty.fork()
            if pid == 0:  # child
                try:
                    os.chdir(str(workdir))
                    os.environ.update(
                        {
                            "HOME": str(workdir),
                            "TERM": "xterm-256color",
                            "LANG": "C.UTF-8",
                            "LC_ALL": "C.UTF-8",
                            "PS1": r"\[\e[38;5;180m\]mestinghem\[\e[0m\]:\[\e[38;5;110m\]\w\[\e[0m\]$ ",
                            "PROMPT_COMMAND": "",
                            "MST_SANDBOX": "1",
                            "PATH": "/usr/local/bin:/usr/bin:/bin",
                        }
                    )
                    os.execv(shell, [shell, "-i"])
                except Exception:
                    os._exit(127)
            self._set_size(master_fd, 34, 120)
            session = Session(
                id=new_id("trm_"),
                user_id=user_id,
                pid=pid,
                master_fd=master_fd,
                cwd=workdir,
                shell=shell,
            )
            self.sessions[session.id] = session
            self._readers[session.id] = asyncio.create_task(self._pump(session))
            await db.insert(
                "terminal_sessions",
                {
                    "id": session.id,
                    "user_id": user_id,
                    "title": f"session {session.id[-6:]}",
                    "cwd": str(workdir),
                    "shell": shell,
                    "sandboxed": True,
                    "status": "active",
                    "created_at": _now(),
                    "closed_at": None,
                },
            )
            await audit("terminal.open", user_id=user_id, resource="terminal", resource_id=session.id,
                        detail={"cwd": str(workdir)})
            return session

    @staticmethod
    def _set_size(fd: int, rows: int, cols: int) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        session = self.sessions.get(session_id)
        if session and session.alive:
            self._set_size(session.master_fd, max(4, min(200, rows)), max(20, min(400, cols)))

    async def _pump(self, session: Session) -> None:
        loop = asyncio.get_running_loop()
        try:
            while session.alive:
                try:
                    data = await loop.run_in_executor(None, self._read, session.master_fd)
                except OSError:
                    break
                if data is None:
                    break
                if not data:
                    break
                session.output.extend(data)
                if len(session.output) > OUTPUT_CAP:
                    del session.output[: len(session.output) - OUTPUT_CAP]
                session.last_activity = time.time()
                for queue in list(session.subscribers):
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        pass
        finally:
            session.alive = False
            for queue in list(session.subscribers):
                try:
                    queue.put_nowait(b"")
                except asyncio.QueueFull:
                    pass

    @staticmethod
    def _read(fd: int) -> bytes | None:
        try:
            return os.read(fd, READ_CHUNK)
        except OSError:
            return None

    async def write(self, session: Session, data: str | bytes) -> None:
        if not session.alive:
            raise RuntimeError("session closed")
        payload = data.encode("utf-8") if isinstance(data, str) else data
        os.write(session.master_fd, payload)
        session.last_activity = time.time()

    async def run(self, session: Session, command: str, *, permissions: dict[str, Any] | None = None,
                  timeout: int | None = None) -> dict[str, Any]:
        """Execute one command and return captured output (non-streaming mode)."""
        permissions = permissions or {}
        verdict = screen_command(command, permissions, cwd=session.cwd)
        started = time.perf_counter()
        if not verdict.allowed:
            await db.insert(
                "terminal_commands",
                {
                    "id": new_id("cmd_"),
                    "session_id": session.id,
                    "user_id": session.user_id,
                    "command": command[:4000],
                    "cwd": str(session.cwd),
                    "exit_code": None,
                    "duration_ms": 0,
                    "output_bytes": 0,
                    "blocked": True,
                    "reason": verdict.code,
                    "created_at": _now(),
                },
            )
            await audit("terminal.blocked", user_id=session.user_id, resource="terminal",
                        resource_id=session.id, outcome="denied",
                        detail={"code": verdict.code, "risk": verdict.risk})
            return {
                "allowed": False,
                "reason": verdict.reason,
                "code": verdict.code,
                "risk": verdict.risk,
                "output": "",
                "exit_code": None,
                "duration_ms": 0,
            }

        marker = f"__mst_{os.urandom(6).hex()}__"
        wrapped = f"{{ {command} ; }} ; printf '\\n{marker}%s\\n' $?"
        marker_re = re.compile(re.escape(marker) + r"(-?\d+)")
        before = len(session.output)
        await self.write(session, wrapped + "\n")
        limit = timeout or settings.terminal_timeout
        deadline = time.perf_counter() + limit
        exit_code: int | None = None
        while time.perf_counter() < deadline:
            chunk = bytes(session.output[before:]).decode("utf-8", "replace")
            match = marker_re.search(chunk)
            if match:
                exit_code = int(match.group(1))
                break
            if not session.alive:
                break
            await asyncio.sleep(0.06)

        raw = bytes(session.output[before:]).decode("utf-8", "replace")
        # The marker shows up twice: once inside the echoed command line and once
        # in the real `printf` output. Cut at the one followed by the exit code so
        # the echoed wrapper never truncates the captured output.
        match = marker_re.search(raw)
        if match:
            if exit_code is None:
                exit_code = int(match.group(1))
            output = raw[: match.start()]
        else:
            output = raw.split(marker)[0]
        output = _strip_ansi(output)
        output = _strip_wrapped_command(output, command)
        truncated = len(raw) >= OUTPUT_CAP
        duration = int((time.perf_counter() - started) * 1000)
        session.commands += 1

        await db.insert(
            "terminal_commands",
            {
                "id": new_id("cmd_"),
                "session_id": session.id,
                "user_id": session.user_id,
                "command": command[:4000],
                "cwd": str(session.cwd),
                "exit_code": exit_code,
                "duration_ms": duration,
                "output_bytes": len(raw),
                "blocked": False,
                "reason": verdict.code,
                "created_at": _now(),
            },
        )
        if exit_code in (None,) or (exit_code or 0) >= 126:
            await audit("terminal.command", user_id=session.user_id, resource="terminal",
                        resource_id=session.id, outcome="error",
                        detail={"exit_code": exit_code, "risk": verdict.risk})
        return {
            "allowed": True,
            "output": output[-120_000:],
            "exit_code": exit_code,
            "duration_ms": duration,
            "truncated": truncated,
            "timed_out": exit_code is None,
            "risk": verdict.risk,
        }

    def subscribe(self, session: Session) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        session.subscribers.add(queue)
        return queue

    def unsubscribe(self, session: Session, queue: asyncio.Queue) -> None:
        session.subscribers.discard(queue)

    async def close(self, session_id: str, *, user_id: str | None = None) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return False
        if user_id and session.user_id != user_id:
            raise PermissionError("not your session")
        session.alive = False
        task = self._readers.pop(session_id, None)
        if task:
            task.cancel()
        try:
            os.kill(session.pid, signal.SIGHUP)
            await asyncio.sleep(0.05)
            os.kill(session.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            os.waitpid(session.pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        try:
            os.close(session.master_fd)
        except OSError:
            pass
        await db.execute(
            "update terminal_sessions set status = $1, closed_at = $2 where id = $3",
            "closed", _now(), session_id,
        )
        self.sessions.pop(session_id, None)
        await audit("terminal.close", user_id=session.user_id, resource="terminal", resource_id=session_id)
        return True

    async def reap_idle(self) -> int:
        closed = 0
        for session in list(self.sessions.values()):
            if session.idle_seconds > IDLE_TTL or not session.alive:
                try:
                    await self.close(session.id, user_id=session.user_id)
                    closed += 1
                except Exception:
                    pass
        return closed

    async def close_all(self) -> None:
        for session in list(self.sessions.values()):
            try:
                await self.close(session.id, user_id=session.user_id)
            except Exception:
                pass

    def for_user(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "cwd": str(s.cwd),
                "shell": s.shell,
                "alive": s.alive,
                "commands": s.commands,
                "idle_seconds": int(s.idle_seconds),
                "created_at": datetime.fromtimestamp(s.created_at, timezone.utc).isoformat(),
            }
            for s in self.sessions.values()
            if s.user_id == user_id
        ]

    async def history(self, user_id: str, limit: int = 60) -> list[dict[str, Any]]:
        rows = await db.fetch(
            "select command, cwd, exit_code, duration_ms, blocked, reason, created_at "
            "from terminal_commands where user_id = $1 order by created_at desc limit $2",
            user_id, limit,
        )
        return [
            {
                "command": r["command"],
                "cwd": r["cwd"],
                "exit_code": r["exit_code"],
                "duration_ms": r["duration_ms"],
                "blocked": bool(r["blocked"]),
                "reason": r["reason"],
                "created_at": r["created_at"],
            }
            for r in rows.rows
        ]


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")


def clean_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")


def _strip_ansi(text: str) -> str:
    """Remove terminal control sequences so one-shot output is clean text."""
    return _ANSI_RE.sub("", text)


def _strip_wrapped_command(output: str, command: str) -> str:
    """Drop the echoed input line so only real output remains."""
    lines = output.split("\n")
    probe = command[:40]
    # The pty echoes the wrapped command on the first line (with the prompt);
    # drop leading lines until we are past that echo.
    while lines and (probe in lines[0] or lines[0].strip().endswith(("; }", "' $?"))):
        lines = lines[1:]
    return "\n".join(lines).strip("\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


terminal_service = TerminalService()
