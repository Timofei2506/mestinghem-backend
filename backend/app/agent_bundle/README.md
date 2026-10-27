# Mestinghem AI — Device Agent

This bundle contains a cross-platform reference agent that connects your
computer to the Mestinghem AI platform. It runs on Windows, macOS and Linux
with **Python 3.9+** and uses only the standard library.

The agent never acts on its own. It executes only the actions **you approved**
in the web app, strictly within the permission profile and trusted folders you
selected during pairing.

## Quick start

1. **Pair the device in the web app**
   Open *Agent → Connect device*, choose a permission profile
   (Observer / Assistant / Operator / Full) and the trusted folders, then press
   *Pair*. The app shows a **device_id** and a **device_key** — the key is
   displayed once.

2. **Create your config**
   Copy `config.example.json` to `config.json` and fill in the values:

   ```json
   {
     "base_url": "https://your-mestinghem-host",
     "device_id": "dev_...",
     "device_key": "..."
   }
   ```

3. **Run the agent**

   ```bash
   python mestinghem_agent.py          # keep running
   python mestinghem_agent.py --once   # one poll cycle, then exit
   ```

   The device turns **online** in the web app and starts accepting your
   approved actions.

## Permission profiles

- **Observer** — read only: system info, window list, screen view, reading files
  inside trusted folders.
- **Assistant** — plus launching apps and reading the clipboard.
- **Operator** — plus writing files in trusted folders, changing settings,
  closing apps, writing the clipboard.
- **Full** — the whole action set; write operations still require your approval.

Risky and critical actions always wait for your explicit confirmation, and
actions that could cause irreparable harm are blocked entirely.

## What this reference build executes locally

`system.info`, `system.health`, `files.list`, `files.read` (inside trusted
folders). Window/app enumeration, screen capture and clipboard access are
acknowledged but require the native build, which is delivered as a packaged
`.exe`/`.app` in a later release.

## Security notes

- The device key is a secret. Keep `config.json` private; do not commit it.
- All traffic runs over TLS and is authenticated with the `X-Device-Key` header.
- Commands are signed by the platform (HMAC-SHA256 over a canonical envelope).
- You can revoke the device at any time from the web app; the agent stops
  immediately on the next heartbeat.

## Uninstall

Stop the agent (Ctrl+C) and delete this folder. Revoking the device in the web
app removes its access permanently.

---

Mestinghem AI UAB · Vilnius, Lithuania · support@mestinghem.ai
