# Remote access — Microsoft Dev Tunnels

`harbin serve` runs the TUI in a browser via WebSocket using `textual-serve`.
For public exposure, harbin pairs with **Microsoft Dev Tunnels** rather
than binding to a public interface directly. This document covers the
one-time install and the daily flow.

## Why Dev Tunnels?

- `textual-serve` ships **no authentication**.
- Dev Tunnels gives you a stable HTTPS endpoint backed by Microsoft
  identity (GitHub login by default).
- Binding harbin to `0.0.0.0` works, but exposes an unauthenticated
  WebSocket to the network. Don't do this unless you control the
  network and understand the tradeoff.

## Install

### Windows

```powershell
winget install Microsoft.devtunnel
```

### macOS

```bash
brew install --cask devtunnel
```

### Linux

```bash
curl -sL https://aka.ms/DevTunnelCliInstall | bash
```

Or follow the [official Microsoft instructions](https://learn.microsoft.com/azure/developer/dev-tunnels/get-started).

## One-time authentication

```bash
devtunnel user login -g          # opens a browser for GitHub OAuth
devtunnel user show              # verify
```

## Daily flow

```bash
# 1. start harbin (loopback only — the safe default)
harbin

# 2. inside the TUI, type:
> /tunnel start
# harbin runs `devtunnel host -p 8080 --allow-anonymous false`
# and reports the public https://… URL on the next line.

> /tunnel status
# shows the public URL again
> /tunnel stop
# terminates the devtunnel subprocess
```

### Named tunnels (optional)

If you want a stable subdomain across restarts:

```bash
devtunnel create harbin
devtunnel port create -p 8080 harbin
```

Then set `tunnels.tunnel_id: harbin` in `config.yaml` (or via
`/config → Dev Tunnels`).

## Notes

- The devtunnel subprocess **outlives** harbin. If harbin exits, the
  tunnel keeps running. Stop it with `/tunnel stop` (or
  `devtunnel tunnel delete` when started externally).
- harbin **never** drives an interactive `devtunnel user login` itself.
  Run it in your own shell.
- The tunnel only proxies to whatever port `harbin serve` (or the
  TUI process, if `web.autostart: true`) is binding. If nothing is
  bound, the tunnel will appear up but you'll get a connection error.
