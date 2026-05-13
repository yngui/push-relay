# Push Relay

Forward notifications from your machines to your phone via Web Push, with no
backend you have to operate. Single-user, single-VAPID-keypair, multi-client.

## How it works

```
[ source: Windows toast / GHCP webhook / cron / ... ]
     -> client process running on your machine
     -> push_relay.send_web_push  (VAPID JWT + RFC 8291 encryption)
     -> https://<push-service>/...     (FCM / Mozilla / Apple)
     -> phone (PWA service worker)
     -> showNotification(...)
```

The only persistent web infrastructure is a static page on GitHub Pages used
**once per device** to bootstrap a `PushSubscription`. After that, every
notification is a direct outbound HTTPS POST from your client process to the
phone's push endpoint.

No server. No API keys. No accounts. The page is public; subscriptions are
useless without your VAPID private key.

## Layout

```
push-relay/
  pwa/                    static subscribe page (GitHub Pages)
    icons/apps/           per-app notification icons (PNG)
  push_relay/             shared Python package (crypto + DPAPI helpers)
  clients/
    windows-toast/        first client: forwards Action Center toasts
  tools/
    new-vapid.py          generate VAPID keypair (DPAPI-encrypted at rest)
    add-sub.py            paste subscription JSON -> client's subs.json
    extract-app-logo.py   capture an app's logo into pwa/icons/apps/
    test-send.py          synthetic push for verification
  .github/workflows/      Pages deploy
  pyproject.toml
```

Per-machine state lives at `~/.push-relay/`:

```
~/.push-relay/
  vapid.json.dpapi         # shared by all local clients
  windows-toast/
    venv/                  # client venv with push_relay installed editable
    windows_toast.py       # snapshot of the client script
    subs.json              # array of {endpoint, keys, label, added}
    listener.log
    notify.log
    seen-ids.txt
```

## Setup

Prereqs (one-time, Windows):

| | Install |
|---|---|
| Python 3.11+ | `winget install -e --id Python.Python.3.13` |
| Git | `winget install -e --id Git.Git` |

Clone:

```powershell
git clone https://github.com/<user>/push-relay.git C:\src\push-relay
cd C:\src\push-relay
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

Generate a VAPID keypair (DPAPI-encrypts the full JSON to
`~/.push-relay/vapid.json.dpapi`; prints the public key for the PWA):

```powershell
.\.venv\Scripts\python tools\new-vapid.py mailto:you@example.com
```

Paste the printed public key into `pwa/config.js`:

```js
window.VAPID_PUBLIC = "<paste>";
```

Commit + push so GitHub Pages picks it up:

```powershell
git add pwa/config.js
git commit -m "vapid public"
git push
```

Enable Pages once at https://github.com/<user>/push-relay/settings/pages →
**Source: GitHub Actions**.

## Install a client

### `windows-toast` — forward Windows Action Center toasts

```powershell
pwsh -File .\clients\windows-toast\install.ps1
```

This:
- Creates `~/.push-relay/windows-toast/venv/`.
- Installs `push_relay` (editable from the repo) and the WinRT projection.
- Snapshot-copies the client script.
- Registers Scheduled Task `PushRelay-WindowsToast` (logon, hidden).
- Disables any pre-existing `ToastPushListener` task (does not delete).

Verify:

```powershell
Get-Content "$env:USERPROFILE\.push-relay\windows-toast\listener.log" -Tail 5
# Expect: Startup ... Push enabled ... Initial notifications: N ... Entering polling loop
```

To customize the app filter, edit `clients/windows-toast/windows_toast.py`
(`APP_REGEX`, `BODY_REGEX`, rate limit) then re-run `install.ps1`.

## Subscribe a phone

1. Open `https://<user>.github.io/push-relay/` on Android Chrome (or Safari
   16.4+ via Add to Home Screen for iOS).
2. Tap **Enable notifications** → Allow.
3. Tap **Copy subscription** (or copy the JSON manually).
4. Send the JSON to your sender PC (chat / email / clipboard via SSH).
5. On the PC, with the JSON in clipboard:

   ```powershell
   .\.venv\Scripts\python tools\add-sub.py --client windows-toast --label "my-pixel"
   ```

`subs.json` is appended; the listener picks it up on the next push (no
restart needed).

Tapping a delivered notification opens the PWA on a detail page showing the
full payload (icon + source line, title, body, sent/received timestamps).

## Per-app icons

Notifications can carry an app-specific icon shown by the phone. Icons are
committed to `pwa/icons/apps/<slug>.png` and referenced from the sender's
mapping (`APP_ICON_AUMID` / `APP_ICON_DISPLAY` in
`clients/windows-toast/windows_toast.py`).

To add one:

```powershell
& "$env:USERPROFILE\.push-relay\windows-toast\venv\Scripts\python.exe" `
  tools\extract-app-logo.py --offline --app-name "^<App Name>$" `
  --out pwa\icons\apps\<slug>.png
```

Paste the printed `APP_ICON_AUMID[...] = ...` line into
`windows_toast.py`, commit + push, then restart the scheduled task. The
phone picks up the new SW + asset on the next visit to the PWA.

## Verify end-to-end

```powershell
.\.venv\Scripts\python tools\test-send.py
```

Sends a synthetic push (with `urgency=high` and the `source` schema) to every
subscription in `~/.push-relay/windows-toast/subs.json`. Pass `--client <name>`
to target a different client.

## Operate

| Task | How |
|---|---|
| Add a device | Subscribe in PWA → `add-sub.py` |
| Remove a device | Edit `~/.push-relay/<client>/subs.json`, delete the entry |
| Rotate VAPID | `new-vapid.py`, update `pwa/config.js`, push, re-subscribe everyone |
| Restart client | `Stop-ScheduledTask PushRelay-WindowsToast; Start-ScheduledTask PushRelay-WindowsToast` |
| View logs | `Get-Content "$env:USERPROFILE\.push-relay\windows-toast\listener.log" -Wait -Tail 0` |
| Update from repo | `git pull; pwsh -File .\clients\windows-toast\install.ps1` |
| Tear down a client | `pwsh -File .\clients\windows-toast\uninstall.ps1` |

## Security

| Item | Where | Sensitive? |
|---|---|---|
| VAPID **public** key | `pwa/config.js` (committed) | public by design |
| VAPID **private** key | `~/.push-relay/vapid.json.dpapi` (DPAPI CurrentUser) | secret; same-user same-machine only |
| Subscriptions | `~/.push-relay/<client>/subs.json` | moderately — needed with VAPID private to deliver |
| Subscribe page | `https://<user>.github.io/push-relay/` | public; visiting just generates a sub on the visitor's device, doesn't reach you |

The subscribe page being public is harmless: a stranger who opens it gets a
`PushSubscription` for **their own** browser. They cannot send to it (no VAPID
private), and you don't know it exists (no automated transfer).

## Adding a new client

1. Create `clients/<name>/`.
2. Write the client script (whatever language; Python recommended for
   `push_relay` reuse).
3. Add `install.ps1` (or platform equivalent) modeled on `windows-toast/`.
4. Document the section in the table under "Install a client" above.

The shared `push_relay` package handles all the crypto + sub-store + DPAPI.
A client just needs to decide *what* to push.

When calling `send_web_push`, pass `urgency="high"` for prompt delivery
(wakes phone from Doze; RFC 8030 max), and include an optional `icon` field
in the JSON payload pointing to a path under `pwa/icons/` for a per-message
icon. The optional `source` payload field is a free-form `{key: string}`
object; values are joined by `·` in the lock-screen body and the detail page
header.

## Troubleshooting

- **`Push disabled: VAPID load failed`** — run `tools/new-vapid.py` (or copy
  an existing `vapid.json.dpapi` from another machine — it won't decrypt
  unless you're the same Windows user).
- **`Push sent: ok=0/1 failed=[<label>=410]`** — the subscription is stale
  (Chrome rotated the endpoint, user cleared site data, etc.). Re-subscribe
  the device and `add-sub.py` again. Edit `subs.json` to remove the dead
  entry.
- **`SEEN id=...` but no `MATCH ...`** — `APP_REGEX` filtered the toast.
- **Phone shows no notification but log says `ok=1`** — Android may have
  delivered to the wrong PWA (e.g. an old subscription on a removed app).
  Long-press the dead PWA icon → uninstall, and revoke notification
  permission for any stale origins in Chrome site settings.
- **Service worker won't update on phone** — close all tabs of the PWA;
  reopen. Or clear site data via long-press → App info → Storage.

## Roadmap

- More clients: GHCP webhook listener, generic CLI sender, etc.
- Auto-prune of stale subs.
- Optional cross-platform DPAPI alternative (libsecret on Linux, Keychain on
  macOS).
