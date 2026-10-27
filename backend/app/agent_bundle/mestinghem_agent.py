#!/usr/bin/env python3
"""
Mestinghem AI — device agent (reference implementation).

Connects an already-paired device to the Mestinghem AI platform, polls for
actions the user has APPROVED, executes them strictly within the granted
permission profile and trusted folders, and reports results back.

Design principles
-----------------
* Least privilege: only actions allowed by the device profile are executed.
* Defence in depth: the forbidden-action list and trusted-path rules are
  re-checked locally, even though the server already enforces them.
* No secrets in code: the device key lives in config.json next to this file.
* Standard library only: no third-party packages are required.

Usage
-----
    python mestinghem_agent.py            # run with ./config.json
    python mestinghem_agent.py --once     # single poll cycle, then exit

Pairing
-------
1. In the web app open Agent -> Connect device, choose a permission profile
   and trusted folders, and press "Pair".
2. Copy the device_id and device_key into config.json (see config.example.json).
3. Run this script. The device turns "online" and awaits your approved actions.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AGENT_VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

# Actions this reference agent knows how to perform locally. Anything else that
# the server might queue is acknowledged as unsupported rather than executed.
SUPPORTED = {
    "system.info", "system.health", "windows.list", "apps.list",
    "files.list", "files.read", "screen.view", "clipboard.read",
}

# Hard-denied regardless of profile — mirrors the platform policy.
FORBIDDEN = {
    "input.keylog", "credentials.read", "browser.passwords", "camera.capture",
    "microphone.record", "payment.execute", "mail.send", "network.exfiltrate",
    "process.kill_system", "registry.write", "service.install", "firmware.flash",
}


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        sys.exit(
            "config.json not found. Copy config.example.json to config.json and "
            "fill in base_url, device_id and device_key from the pairing screen."
        )
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key in ("base_url", "device_id", "device_key"):
        if not cfg.get(key):
            sys.exit(f"config.json is missing required field: {key}")
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    return cfg


class Client:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.device_id = cfg["device_id"]
        self.headers = {
            "Content-Type": "application/json",
            "X-Device-Key": cfg["device_key"],
            "User-Agent": f"mestinghem-agent/{AGENT_VERSION}",
        }

    def _url(self, path: str) -> str:
        return f"{self.cfg['base_url']}/api/agent/agent/{self.device_id}{path}"

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._url(path), data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {exc.code} on {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"network error on {path}: {exc.reason}") from exc

    def manifest(self) -> dict:
        return self._request("GET", "/manifest")

    def poll(self) -> dict:
        return self._request("GET", "/poll?limit=10")

    def heartbeat(self) -> dict:
        return self._request("POST", "/heartbeat", {
            "hostname": socket.gethostname(),
            "agent_version": AGENT_VERSION,
        })

    def result(self, action_id: str, ok: bool, result: dict | None = None,
               error: str | None = None, duration_ms: int = 0) -> dict:
        return self._request("POST", "/result", {
            "action_id": action_id, "ok": ok,
            "result": result or {}, "error": error, "duration_ms": duration_ms,
        })


# ---------------------------------------------------------------------------
# Local action handlers (read-only / low-risk by design)
# ---------------------------------------------------------------------------
def _within_trusted(path: str, trusted: list[str]) -> bool:
    if not trusted:
        return False
    target = os.path.abspath(os.path.expanduser(path.replace("\\", "/")))
    for root in trusted:
        root_abs = os.path.abspath(os.path.expanduser(root.replace("\\", "/")))
        if target == root_abs or target.startswith(root_abs + os.sep):
            return True
    return False


def handle_system_info(payload: dict, ctx: dict) -> dict:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "agent_version": AGENT_VERSION,
    }


def handle_system_health(payload: dict, ctx: dict) -> dict:
    usage = shutil.disk_usage(str(Path.home()))
    return {
        "disk_total_gb": round(usage.total / 1e9, 2),
        "disk_used_gb": round(usage.used / 1e9, 2),
        "disk_free_gb": round(usage.free / 1e9, 2),
        "load_avg": list(getattr(os, "getloadavg", lambda: (0, 0, 0))()),
    }


def handle_files_list(payload: dict, ctx: dict) -> dict:
    path = str(payload.get("path") or "")
    if not _within_trusted(path, ctx["trusted_paths"]):
        raise PermissionError("path is outside the trusted folders")
    entries = []
    for entry in sorted(Path(path).iterdir(), key=lambda p: p.name.lower())[:500]:
        try:
            stat = entry.stat()
            entries.append({
                "name": entry.name, "is_dir": entry.is_dir(),
                "size": stat.st_size if entry.is_file() else 0,
                "modified": int(stat.st_mtime),
            })
        except OSError:
            continue
    return {"path": path, "entries": entries}


def handle_files_read(payload: dict, ctx: dict) -> dict:
    path = str(payload.get("path") or "")
    if not _within_trusted(path, ctx["trusted_paths"]):
        raise PermissionError("path is outside the trusted folders")
    limit = int(payload.get("max_bytes") or 256_000)
    data = Path(path).read_bytes()[:limit]
    try:
        text = data.decode("utf-8")
        return {"path": path, "text": text, "truncated": len(text) >= limit}
    except UnicodeDecodeError:
        import base64
        return {"path": path, "base64": base64.b64encode(data).decode(), "binary": True}


def handle_windows_list(payload: dict, ctx: dict) -> dict:
    # Cross-platform placeholder: a native build enumerates real windows.
    return {"windows": [], "note": "window enumeration requires the native agent build"}


def handle_apps_list(payload: dict, ctx: dict) -> dict:
    return {"apps": [], "note": "app enumeration requires the native agent build"}


def handle_screen_view(payload: dict, ctx: dict) -> dict:
    return {"image": None, "note": "screen capture requires the native agent build"}


def handle_clipboard_read(payload: dict, ctx: dict) -> dict:
    return {"text": "", "note": "clipboard access requires the native agent build"}


HANDLERS = {
    "system.info": handle_system_info,
    "system.health": handle_system_health,
    "files.list": handle_files_list,
    "files.read": handle_files_read,
    "windows.list": handle_windows_list,
    "apps.list": handle_apps_list,
    "screen.view": handle_screen_view,
    "clipboard.read": handle_clipboard_read,
}


def execute(command: dict, ctx: dict) -> tuple[bool, dict, str | None]:
    action = command["action"]
    payload = command.get("payload") or {}
    if action in FORBIDDEN:
        return False, {}, "action is forbidden by platform policy"
    if action not in ctx["scopes"]:
        return False, {}, f"action '{action}' is outside the granted profile"
    handler = HANDLERS.get(action)
    if handler is None:
        return False, {}, f"action '{action}' is not supported by this agent build"
    data = handler(payload, ctx)
    return True, data, None


def run_cycle(client: Client, ctx: dict) -> int:
    poll = client.poll()
    ctx["scopes"] = set((poll.get("policy") or {}).get("scopes") or ctx["scopes"])
    ctx["trusted_paths"] = (poll.get("policy") or {}).get("trusted_paths") or ctx["trusted_paths"]
    handled = 0
    for command in poll.get("commands") or []:
        started = time.perf_counter()
        try:
            ok, data, err = execute(command, ctx)
        except Exception as exc:  # noqa: BLE001
            ok, data, err = False, {}, str(exc)[:300]
        duration = int((time.perf_counter() - started) * 1000)
        try:
            client.result(command["action_id"], ok, data, err, duration)
        except RuntimeError as exc:
            print(f"  ! could not report result: {exc}", file=sys.stderr)
        handled += 1
    return handled


def main() -> int:
    parser = argparse.ArgumentParser(description="Mestinghem AI device agent")
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    args = parser.parse_args()

    cfg = load_config()
    client = Client(cfg)
    try:
        man = client.manifest()
    except RuntimeError as exc:
        print(f"Could not reach the platform: {exc}", file=sys.stderr)
        return 1

    ctx = {
        "scopes": set(man.get("scopes") or []),
        "trusted_paths": man.get("trusted_paths") or [],
        "profile": man.get("profile"),
    }
    print(f"Mestinghem AI agent {AGENT_VERSION}")
    print(f"  device   : {client.device_id}")
    print(f"  profile  : {ctx['profile']}")
    print(f"  scopes   : {', '.join(sorted(ctx['scopes'])) or 'none'}")
    print(f"  trusted  : {', '.join(ctx['trusted_paths']) or 'none'}")

    interval = int(man.get("poll_interval_seconds") or 5)
    if args.once:
        client.heartbeat()
        n = run_cycle(client, ctx)
        print(f"  handled {n} action(s); exiting (--once)")
        return 0

    print("  status   : online — waiting for approved actions (Ctrl+C to stop)")
    try:
        while True:
            try:
                hb = client.heartbeat()
                if hb.get("revoked"):
                    print("Device was revoked by the owner. Stopping.", file=sys.stderr)
                    return 0
                run_cycle(client, ctx)
            except RuntimeError as exc:
                print(f"  ! {exc} — retrying", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nAgent stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
