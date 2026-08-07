# Supported Windows-mod runtime on an x86-64 Linux host

PalDefender is published only for the Windows Palworld dedicated server. The
normal PalWorldSelfHost deployment therefore remains `native-linux`; DLL mods
are never copied into or attempted against that process. Operators who need the
PalDefender/UE4SS outcomes can deliberately select `wine-windows`, which runs
Pocketpair's current Windows depot under Wine while retaining the same systemd,
settings, backup, REST, RCON firewall and operations-console boundaries.

This path was validated on 2026-07-16 with Wine 11.13, Pocketpair build
`v1.0.1.100619`, PalDefender 1.8.3 and Okaetsu's Palworld-specific UE4SS build
`c2ac246` (release asset updated 2026-07-10). It is x86-64 only and deliberately
opt-in. Do not change an existing production runtime in place: take and verify a
protected backup, build a separate instance, import a copy, and prove client
compatibility before scheduling a migration.

## Host preparation

Install a 64-bit Wine runtime, `xvfb-run`, and a host SteamCMD that honors an
absolute `+force_install_dir`. Set these values before running the normal
installer:

```ini
PALWORLD_RUNTIME=wine-windows
PALWORLD_WINE_PREFIX=/var/lib/palworld/wine-prefix
PALWORLD_WINEDEBUG=-all
PALWORLD_WINE_DLL_OVERRIDES="d3d9=n,b;dwmapi=n,b"
PALWORLD_GAME_HOOK_SPOOL=/run/palworld/game-hook
```

`update.sh` then requests Steam app 2394010 with
`+@sSteamCmdForcePlatformType windows`; `start.sh` renders
`Config/WindowsServer`, uses an off-screen X server and launches
`PalServer.exe`. The native-first DLL overrides are required for the two
reviewed proxy loaders and fall through harmlessly when a component is absent.
Direct systemd stops request an authenticated save and REST stop before the
ordinary timeout/kill fallback.

Expose only the game/query UDP ports. Pocketpair REST, RCON, the operations
console, the Discord chat ingress and the volatile game-hook spool remain local
control-plane facilities.

## Loader lifecycle

The Mods page and `scripts/mod-lifecycle.py` provide the same plan/apply
contract:

```bash
scripts/mod-lifecycle.py status
scripts/mod-lifecycle.py plan paldefender install
scripts/mod-lifecycle.py plan ue4ss install
```

Review the returned release source, artifact SHA-256, managed files and plan
hash. Stop the world, then execute with the returned hash and exact phrase
(`INSTALL PALDEFENDER`, `INSTALL UE4SS`, `REMOVE …`, or `ROLLBACK …`). Execution
downloads only the allowlisted GitHub HTTPS asset, bounds and validates its ZIP,
verifies the pinned archive hash, rejects links/traversal/duplicates, preserves
the prior runtime files, and verifies every installed file. Drift and an active
server fail closed.

PalDefender's generated configuration is never removed. UE4SS settings and
operator Lua mods are seeded only when absent and survive install, update,
remove and rollback. The shipped `PalWorldSelfHostRelay` Lua hook is managed
with UE4SS.

## Volatile chat relay

When the existing Discord chat relay is enabled, the Lua hook writes bounded
chat events to the configured `/run` or `/dev/shm` directory. It performs no
network I/O and receives no Discord/bearer credential. The host
`palworld-game-hook.service` validates those tmpfs events, forwards enabled
categories to the bearer-authenticated loopback ingress, retries transient
server failures and deletes accepted, ignored or permanently rejected events.
Chat bodies never enter the audit log or persistent replay state.

After configuring the Discord bot, chat-relay token and categories, enable both
host services:

```bash
sudo systemctl enable --now palworld-bot.service
sudo systemctl enable --now palworld-game-hook.service
```

The shared environment keeps `PALWORLD_GAME_HOOK_SPOOL` as a Linux `/run` or
`/dev/shm` path. The managed Lua hook translates it to Wine's `Z:` view; do not
put a persistent directory or a credential in that setting.

If UE4SS reports that the game hook no longer exists after a Palworld update,
the loader logs the failure and the server continues without relay. Keep the
relay disabled until a reviewed Palworld-specific UE4SS release is pinned and
validated; never substitute generic or unreviewed DLLs.
