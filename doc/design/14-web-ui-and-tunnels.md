# web-ui-and-tunnels — Sub-spec

> Status: **draft** · scope: `harbin serve` boot, textual-serve binding, `/tunnel` wrapper for `devtunnel host`.
> Parent: [`design-overview.md`](./design-overview.md).

Harbin's web UI is the same Textual app, served over WebSocket via `textual-serve`. There is no second frontend; styling and behavior follow the local TUI automatically. Public exposure is opt-in and is expected to go through Microsoft Dev Tunnels rather than a bare port.

---

## 1 · `harbin serve`

Subcommand of the single `harbin` console script (see [`01-project-layout`](./01-project-layout.md) §3).

```
harbin serve [--port PORT] [--host HOST]
```

| Option | Default | Source |
|---|---|---|
| `--port` | `config.web.port` (8080) | CLI flag wins |
| `--host` | `config.web.host` (`127.0.0.1`) | CLI flag wins |

Behavior:

1. Run the normal startup sequence ([`04-concurrency-and-errors`](./04-concurrency-and-errors.md) §2) **without** mounting the local Textual app.
2. Call `textual_serve.server.Server(app_target="harbin.tui.app:HarbinApp", port=port, host=host).serve_blocking()`. This binds the socket and serves the app to any connecting client.
3. Print a single line to stderr: `harbin serving on http://<host>:<port>/`.
4. If `--host` is `0.0.0.0` (or otherwise non-loopback), additionally print:
   ```
   warning: serving on a non-loopback interface without authentication.
   prefer 'harbin' (loopback) + `/tunnel start` (devtunnel). see doc/remote-access.md.
   ```
5. Run until SIGINT/SIGTERM, then shut down per the shared shutdown sequence.

### 1.1 Autostart from TUI mode

If `config.web.autostart: true`, the TUI startup also spins up textual-serve as a background coroutine on the configured port. Failures bind-failures log a WARN but **do not** abort TUI startup — the local UI still runs.

### 1.2 Auth posture

- **None.** textual-serve does not ship an auth layer, and harbin does not add one. Loopback-only by default is the safety net.
- The recommended public-exposure path is `/tunnel`, which delegates authentication to Microsoft's identity (overview §6.4).
- Direct WAN exposure on a non-loopback port without a tunnel is **possible but discouraged** and shows the warning above. No login screen exists.

### 1.3 Limitations versus the local TUI

textual-serve renders the Textual app to a browser-side terminal emulator. Implications:

- Mouse and clipboard work via the browser; keyboard chords behave as they do in any terminal in the browser.
- Glyph fidelity depends on the browser font; consider serving a recommended monospace via simple HTML wrapper (post-v1).
- A single textual-serve session is **per-connection** — multiple browsers can connect and each gets an independent harbin TUI session **bound to the same backend state**. State writes coordinate through the single `AppCore` (no extra locking — the event loop is the lock).

---

## 2 · `/tunnel` slash command

The wrapper exposes Microsoft Dev Tunnels (`devtunnel host`) as a managed subprocess. Harbin does **not** embed the tunnel implementation; the user installs `devtunnel` separately (see [`06-packaging-and-install`](./06-packaging-and-install.md) and `doc/remote-access.md`).

```
/tunnel [start|stop|status]
```

Argument resolution: no argument is equivalent to `status`.

### 2.1 `/tunnel start`

1. **Precheck `devtunnel`.** Look up `config.tunnels.devtunnel_path` (default `"devtunnel"`) on PATH. If not found:
   ```
   error: devtunnel binary not found.
   install: see doc/remote-access.md
   ```
   Exit the command with `UserError`. Do not start anything.
2. **Precheck auth.** Run `devtunnel user show` (capture stdout, timeout 5 s).
   - On success → continue.
   - On failure (any non-zero exit) → print:
     ```
     not logged in to devtunnel. run:
       devtunnel user login -g
     then try /tunnel start again.
     ```
     Exit the command. Harbin does **not** drive interactive auth (overview §6.4).
3. **Determine target port.** If textual-serve is running in this process, use its port. Otherwise use `config.web.port`. If the port is not actually bound, warn and continue (the user may have an external harbin serve).
4. **Spawn.** Build the command:
   ```
   <devtunnel_path> host -p <port> --allow-anonymous <bool>
   ```
   plus `--tunnel-id <id>` when `config.tunnels.tunnel_id` is set.
5. **Capture stdout** with a regex (`https://[a-z0-9-]+\.[a-z0-9-]+\.devtunnels\.ms/?`) to learn the public URL. The first matching line is stored on `TunnelManager.public_url` and surfaced in the Console:
   ```
   tunnel started: https://abc-1234.usw2.devtunnels.ms/
   ```
6. **Lifetime decoupling.** The devtunnel subprocess is **not** added to harbin's TaskGroup. If harbin exits, the tunnel keeps running. If the tunnel dies, harbin keeps running and surfaces a Console line. This matches overview §6.4 ("its lifetime is not coupled to harbin's").

### 2.2 `/tunnel status`

- Reports `running | not running | unknown` along with the cached public URL.
- "Unknown" means harbin's TunnelManager has no record of having started one in this process; an externally-launched tunnel is not detected.

### 2.3 `/tunnel stop`

- Terminates the subprocess (SIGTERM, 5 s grace, SIGKILL).
- Clears `TunnelManager.public_url`.
- Tunnel subprocesses started in a previous harbin invocation are **not** discoverable and can only be stopped by hand or via `devtunnel tunnel delete`.

---

## 3 · `TunnelManager`

A small in-process subsystem (`harbin.web.tunnels.TunnelManager`) that owns at most one `subprocess.Popen` handle.

State:

```python
class TunnelManager:
    process: subprocess.Popen | None = None
    public_url: str | None = None
    started_at: datetime | None = None
```

- Constructed once in startup, after `Config` is loaded.
- All state mutations happen on the event loop.
- The subprocess is launched with `start_new_session=True` (POSIX) / `CREATE_NEW_PROCESS_GROUP` (Windows) so it survives harbin exiting cleanly.

---

## 4 · `doc/remote-access.md`

A standalone user-facing guide, referenced by `/tunnel`'s help text. Not part of this design folder — it's prose for end users, not a sub-spec. Its outline (for completeness):

1. Why Dev Tunnels instead of direct port exposure.
2. Install per-OS (`winget`, `brew`, apt repo / `curl ... | sh`).
3. One-time auth: `devtunnel user login -g`.
4. (Optional) Create a named tunnel: `devtunnel create harbin && devtunnel port create -p 8080 harbin`. Set `config.tunnels.tunnel_id`.
5. Daily flow: `harbin` → `/tunnel start`.

The guide is the source for how-to-install-devtunnel; this sub-spec only specifies how harbin **uses** it.

---

## 5 · Open questions

- Whether to add a `tunnel:` field to `fleet.yaml` for "auto-start a tunnel when this fleet is registered". Deferred — too niche.
- Whether `/tunnel start` should auto-launch `harbin serve` if it's not running. Today: no (separation of concerns). Re-evaluate after first usage.

## 6 · Out of scope (v1)

- Embedding `devtunnel` itself; harbin only wraps the binary.
- Driving interactive `devtunnel user login` from within harbin (overview §6.4).
- Alternative tunnels (`ngrok`, `cloudflared`, `tailscale funnel`). User can run them by hand; `/tunnel` is devtunnel-specific.
- HTTP-level authentication in textual-serve.
- TLS termination (delegated to the tunnel provider).
