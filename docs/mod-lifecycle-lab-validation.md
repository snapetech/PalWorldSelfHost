# Wine mod-runtime and lifecycle validation

Validated on 2026-07-16 in `/home/keith/.cache/palworld-wine-lab`, a disposable
local workstation directory. Production `kspls0` was not stopped, configured or
modified. One read-only Level copy was used only to confirm current internal Pal
IDs after the empty lab world rejected a spawn; matching remote-before,
remote-after and local hashes proved consistency, and that copy was deleted
immediately after decoding.

## Reviewed runtime and artifacts

SteamCMD installed the current Windows depot for app 2394010. Wine 11.13 ran
`PalServer.exe` off screen on private lab ports. Pocketpair REST reported
`v1.0.1.100619` and a disposable world GUID; the vanilla baseline saved and
stopped cleanly before any DLL was added.

The lifecycle catalog pins:

| Component | Reviewed release | Archive SHA-256 | Installed runtime hashes |
| --- | --- | --- | --- |
| PalDefender | `Ultimeit/PalDefender@v1.8.3` | `2ec395237018b18b91b6e25ed1338f9e203814a6a3fd5fec8408eba10bfb35c8` | `PalDefender.dll` `3a8e99…2097`; `d3d9.dll` `0078ba…221f` |
| UE4SS | `Okaetsu/RE-UE4SS@experimental-palworld`, Palworld asset updated 2026-07-10 | `7c80b2f4a29baf0f384552c8517e58196e78c8a1b8530637b7179eddae1b54a9` | `UE4SS.dll` `afc83a…b798`; `dwmapi.dll` `6c6e71…cde1` |

The two upstream sources are the same Windows-only PalDefender and
Palworld-specific UE4SS sources used by the qualifying peer. The normal native
Linux process remains the default and rejects DLL lifecycle operations.

## Real lifecycle and load proof

`scripts/mod-lifecycle.py` performed a real download/install for both
components after an exact plan. It verified the HTTPS redirect boundary,
archive SHA, bounded ZIP shape and each installed file. A running PalServer was
detected and refused; stopped execution created private prior-version backups
and a hash-bound registry.

The managed files started together. PalDefender logged current game version
`v1.0.1.100619`, loaded version `1.8.3`, and RCON `version` returned plugin build
`1.8.3.4014`. UE4SS logged `v3.0.1 Beta #0`, Git SHA `c2ac246`, installed its
game hooks and started six bundled Lua mods. Authenticated Pocketpair REST and
PalDefender RCON remained healthy with the same disposable world GUID.

Both components were then removed by exact-confirmed plans. PalDefender's
generated `Config.json`, UE4SS settings and `mods.txt` hashes were unchanged;
only managed runtime/adapter files disappeared. Exact rollback restored both
components and their registry records. A second server start loaded
PalDefender, UE4SS and the same world again.

Unit coverage additionally refuses traversal, symbolic links, duplicate/large
archives, stale plan hashes, wrong confirmation, unmanaged removal, active
servers and managed-file drift. UE4SS updates overwrite only pinned runtime and
PalWorldSelfHost adapter files; settings, bundled enable state and operator Lua
trees remain value-for-value.

The real administrator Mods page was rendered in local Chromium at 1440×900 and
390×844 with supported-runtime, stopped-world and two managed-component state.
Both views exposed six install/update/remove/rollback planning controls, the
mobile panel remained 356 pixels wide, and neither viewport had horizontal
document overflow. The reviewed desktop/mobile captures and temporary browser
profile were removed after visual inspection.

## Current configuration and command proof

The first live-generated PalDefender 1.8.3 config exposed catalog drift. The
removed tower-boss/PvP-player keys were dropped, three current exploit-protection
booleans and a bounded whitelist message were added, leaving 57 typed scalar
fields plus MOTD. A real hash-bound `logRCON=false → true` apply preserved all
ten unowned keys, `reloadcfg` acknowledged the merge, and rollback plus a second
reload restored the original value.

Current `getrconcmds` uses `name:required_args;…`, not the older fixture token
format. The parser was corrected and live discovery advertised all five typed
actions: `tp`, `spawnpal`, `give`, `givepal` and `givestats`, plus `getpos`.
PalDefender returned `Invalid PalID` for its own generated `Anubis` example and
for IDs verified in the production-derived read-only catalog. That response is
now treated as an application failure, never a successful command
acknowledgement. Successful online teleport/grant and empty-world spawn are not
claimed by this lab; their action/observation paths retain direct fixture proof.

## Volatile game hook

The managed UE4SS install added `PalWorldSelfHostRelay`. On the final real start
UE4SS logged both `Mod 'PalWorldSelfHostRelay' has enabled.txt, starting mod`
and `BroadcastChatMessage hook registered`. The Lua hook receives no token and
performs no network I/O. It bounds and atomically spools JSON events only to a
configured tmpfs directory.

`game-hook-adapter.py` accepts only ordinary, allowlisted, 4-KiB events from
`/run` or `/dev/shm`, validates schema/player/message/category bounds, creates a
stable replay ID, and sends only to the bearer-authenticated loopback relay.
An actual local HTTP drill proved bearer/body forwarding and deletion;
disabled categories were deleted without forwarding, 5xx responses remained
for retry, and 4xx/malformed/oversized events were removed. Chat bodies never
enter audit or persistent replay files.

Confidence is **high** for the Wine runtime, loader/config lifecycle, live
component loading, command discovery, fail-closed action reporting and volatile
hook/host-adapter boundary. Confidence is **moderate** for player-present live
action execution and real Discord-guild delivery until an expendable online
player and an explicitly authorized guild are available.
