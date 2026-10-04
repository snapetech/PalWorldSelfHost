# PalWorldSelfHost

Production-oriented Palworld dedicated-server hosting for Linux. It installs
the official SteamCMD server, manages it with systemd, performs graceful nightly
maintenance, creates verified local and rclone backups, and includes private
operations and public read-only status pages.

Support PalWorldSelfHost development and hosting on [Ko-fi](https://ko-fi.com/snapetech).

## Features

- Native SteamCMD installation with automatic updates
- Loopback-only first-run browser install or exact-fingerprint adoption
- Digest-pinned Pocketpair official-image Compose deployment with persistent saves
- systemd service with restart-on-failure
- Graceful save, announcement, shutdown, update, backup, and restart workflow
- Nightly maintenance targeting 05:00 local time
- SHA-256 and archive verification before offsite upload
- Configurable rclone destination and optional LVM enforcement
- Ten-minute service, REST, listener, and backup-freshness health checks
- Role-aware private operations console with guarded controls, backup inventory,
  telemetry charts, redacted logs and event history
- Public player count, uptime, maintenance schedule, and live-coordinate map
- No public exposure of player IPs, platform identifiers, REST credentials, or
  administrative endpoints
- Shared mutation lock, persisted maintenance phases, and append-only audit log
- Player-aware update deferral with in-game warnings and a maximum wait
- Tiered verified backups, manifests, restore plans, automatic rollback, and drills
- Typed settings plans, automatically restored timed events, moderation, scheduled jobs, and activity history
- Source/SHA-pinned read-only save intelligence for offline profiles, inventory,
  Pals/Paldeck, guilds, bases and private coordinate layers
- Fail-closed private REST/RCON firewall, audited command catalog/history,
  saved/recurring commands, and a transactional enforced player allow-list
- Preservation-first configuration corruption and co-op WorldOptions recovery
- Webhook/Discord/ntfy/Gotify alerts plus optional allowlisted Matrix and
  guild-scoped Discord control bots
- Redacted diagnostics, preflight, migration, safe uninstall, CI, and releases
- Categorized presets plus guarded raw INI diff/apply/rollback with secret preservation
- English, Simplified Chinese, and Japanese private-console operation with
  browser-persisted locale selection and localized `palworldctl` command aliases

This toolkit intentionally operates exactly one Palworld world. It does not include
multi-instance orchestration or unattended Steam build downgrades. Auto-pause is
available as an explicit opt-in and remains disabled by default.

The example configuration uses a 50% XP rate, no raids, 25 Pals per base, and no
death drops. Change those values for your own community. Palworld exposes one
shared `ExpRate`; it does not provide separate stock player and Pal XP
multipliers.

## Requirements

- A modern Linux distribution with systemd
- `steamcmd`, `rclone`, `zstd`, `nftables`, Python 3, `python3-websocket`,
  `curl`, and standard GNU utilities
- Root access for installation
- An existing rclone remote for offsite backups
- UDP 8211 forwarded to the server if it should be internet-accessible

Keep TCP 8212 (Pocketpair REST), TCP 8213 (operations console), and TCP 25575
(RCON) private. The installer creates a fail-closed nftables boundary for
Palworld's wildcard REST/RCON listeners; do not remove that service while the
world is running.

If LAN clients route the server's public `/32` directly through the Palworld
host, enable `PALWORLD_HAIRPIN_NAT_ENABLED` and set `PALWORLD_LAN_CIDR`,
`PALWORLD_NETWORK_INTERFACE`, `PALWORLD_PUBLIC_IP`, and `PALWORLD_BIND_IP`.
The installer then maintains a UDP-8211-only DNAT rule so replies retain the
public source identity expected by the client. Leave this disabled when the
router already provides working hairpin NAT.

## Install

For a browser-guided first run, clone the repository on the target host and
start the one-shot setup listener from a root terminal:

```bash
git clone https://github.com/snapetech/PalWorldSelfHost.git
cd PalWorldSelfHost
sudo ./scripts/bootstrap-server.py
```

Open the printed loopback URL on that host, or forward it without exposing the
listener: `ssh -L 8214:127.0.0.1:8214 host`. Enter the ephemeral code printed in
the root terminal. The UI can install a fresh server or fingerprint and adopt a
stopped native-Linux server tree. It checks prerequisites, builds a 15-minute
immutable plan, generates independent credentials in the browser, requires an
exact confirmation, streams bounded progress and writes a secret-free completion
receipt. It refuses non-loopback binding and any pre-existing environment file.
After success, save the credentials and stop the one-shot process with Ctrl+C.
If the one-shot process itself is interrupted after writing the environment,
resume from the root terminal with `sudo ./scripts/install.sh`; for adoption,
re-run the adoption plan and pass its fingerprint and exact confirmation again.
The installer reuses a completed adoption receipt only after its critical hashes
still match, so this recovery does not repeat or silently broaden the handoff.

For the configuration-file workflow:

```bash
sudo install -o root -g root -m 0600 \
  config/palworld-server.env.example /etc/palworld-server.env
sudo editor /etc/palworld-server.env
sudo ./scripts/install.sh
```

Generate independent, long random values for `PALWORLD_ADMIN_PASSWORD` and
`PALWORLD_OPS_TOKEN`. Configure the rclone remote before installation and never
commit the resulting environment or rclone configuration.

To attach the toolkit to an existing native-Linux Palworld tree, first stop the
unmanaged server, set `PALWORLD_INSTALL_DIR` in the environment file to that
tree, and review its adoption fingerprint:

```bash
sudo python3 scripts/adopt-existing.py plan \
  --install-dir /path/to/existing/server --state-dir /var/lib/palworld
sudo ./scripts/install.sh --adopt-existing \
  --adoption-fingerprint '<fingerprint from the reviewed plan>' \
  --confirm 'ADOPT EXISTING INSTALL'
```

Adoption verifies the Steam manifest and Linux executables, inventories worlds,
refuses symlinked or changing trees and running unmanaged server processes,
preserves the active INI as the managed raw-settings base, transfers the tree to
the locked `palworld` account, and installs management services without running
SteamCMD. An interrupted browser/installer handoff can resume the same adoption
only while the receipt fingerprint and critical executable hashes still match.
Values protected by the environment file (ports, server identity and
administrative credentials) become authoritative when the managed service starts.

The installer creates the locked-down `palworld` service account, installs the
server under `/srv/palworld/server`, and enables these units:

```text
palworld.service
palworld-maintenance.timer
palworld-health.timer
palworld-ops.service
palworld-save-intelligence.timer
palworld-auto-pause.timer
palworld-game-data.timer
palworld-public-status.timer
palworld-rcon-firewall.service
palworld-hairpin-nat.service
palworld-whitelist.timer
```

Check the deployment with:

```bash
systemctl status palworld.service
systemctl list-timers 'palworld-*'
journalctl -u palworld.service -f
palworldctl status
```

The private console selects English, Simplified Chinese, or Japanese from the
browser language and stores the operator's explicit choice locally. A direct
link can request `?lang=en`, `?lang=zh-Hans`, or `?lang=ja`. CLI commands accept
the same locales and localized aliases, for example:

```bash
palworldctl --locale zh-Hans 帮助
palworldctl --locale ja 状態
```

Machine-readable JSON keys, API routes, and exact safety confirmation phrases
remain stable English contracts in every locale.

## Official container deployment

The supported Compose path runs Pocketpair's reviewed official `amd64` image by
both tag and registry digest. It persists the complete `Saved` tree, applies a
mounted full `PalWorldSettings.ini` atomically, publishes only UDP 8211 and
performs authenticated REST save/shutdown before its process-group fallback.

```bash
cd deploy/container
cp PalWorldSettings.ini.example PalWorldSettings.ini
chmod 600 PalWorldSettings.ini
editor PalWorldSettings.ini
docker compose config --quiet
docker compose up -d
```

Do not publish REST 8212 or delete the named volume. See
[`deploy/container/README.md`](deploy/container/README.md) and the exact-image
[`portability lab record`](docs/portability-lab-validation.md). The Helm chart
under `deploy/helm/palworld-selfhost` has also passed exact-image Ready,
graceful-recycle and same-PVC world validation on Kubernetes 1.35.

For multiple independent worlds on one host, use the supported
[`deploy/multi-instance`](deploy/multi-instance/README.md) manager. It creates a
separate official-image Compose project, persistent world ID, save tree,
settings and collision-checked game/query/loopback-control ports per instance;
start/stop/restart address only that project, and exact-confirmed deletion moves
the complete save tree to retirement instead of destroying it. See the
[`multi-instance runtime evidence`](docs/multi-instance-lab-validation.md).

ARM64 Linux hosts with 4 KiB pages have a separate server-only Box64 path in
[`deploy/arm64/README.md`](deploy/arm64/README.md). It pins qualifying peer
release `v2.6.0` by multi-arch index digest, selects its ARM64 manifest, exposes
only UDP 8211 and refuses non-ARM64 Docker daemons, non-4-KiB pages, permissive
credential files, placeholders, short admin secrets or unknown device profiles.
Run `deploy/arm64/preflight.sh` before pulling or starting it; do not apply that
Compose file over an existing native production installation.

Dedicated-server and co-op world directories can be imported offline without
altering the source. Review the plan, stop play by ensuring the roster is empty,
then execute the exact fingerprint shown by the plan:

```bash
palworldctl import-world plan /mnt/incoming/PalworldWorld
palworldctl import-world import /mnt/incoming/PalworldWorld \
  --fingerprint '<fingerprint from the reviewed plan>' --confirm 'IMPORT WORLD'
```

When more than one managed world folder exists, add
`--target-world <32-character-world-id>` to both commands. Import forces a live
save and verified protected backup, stops the service, preserves any replaced
target, selects the imported world in `GameUserSettings.ini`, and rolls the
prior world and selection back automatically if REST startup verification fails.

The private console also exposes a deliberately bounded offline save-repair
workflow. It currently supports renaming one unambiguous player, migrating one
player UID to an unused UID in the same world, replacing a disposable joined
identity, transferring progression/inventory/party/Palbox data from a read-only
sibling world into a disposable joined target identity, and cleaning duplicate
character records when player-save and guild evidence identify one canonical
instance. Bounded offline editors can update an exact player inventory slot
(including `Money` currency), player level/experience, and an owned Pal's
nickname, level, condensation rank, HP/attack/defense talents and up to four
passive IDs. It also repairs dangling character
and dynamic-item slot references and removes character/item containers or
dynamic items only when the complete Level plus every player save has no
remaining reference. Diagnose or review from the browser, or use the installed
privileged adapter directly:

```bash
sudo /usr/local/lib/palworld/ops-save-repair.sh diagnose
sudo /usr/local/lib/palworld/ops-save-repair.sh plan \
  '{"action":"rename_player","uid":"<32-hex-uid>","new_name":"New name"}'
sudo /usr/local/lib/palworld/ops-save-repair.sh apply \
  '{"action":"rename_player","uid":"<32-hex-uid>","new_name":"New name"}' \
  '<reviewed-sha256>' 'RENAME PLAYER'
```

Plan currently permits only the live-validated Steam build `24181105` and binds every world byte, the
schema signature and the immutable parser hash. Apply rechecks a zero roster,
flushes the active world, rejects any changed fingerprint, verifies a protected
backup of those exact already-flushed bytes, stops the service, mutates a private staged copy and demands decoded
structural equality after re-encoding. The staged world is atomically selected
and must pass REST startup; otherwise the original directory is restored. A
successful operation preserves the prior world below
`$PALWORLD_STATE_DIR/save-repair-preserved/`.

UID migration refuses an existing target UID and target player file. The
separate `replace_player` action supports the co-op/existing-host outcome only
after the target identity has joined: it discards that placeholder, reuses its
instance ID and refuses targets with owned data or a non-empty administered
guild. `cleanup_duplicates` refuses ambiguous canonical identity evidence and
any stale instance still referenced outside the duplicate character/guild
records. `cleanup_graph` refuses duplicate container identities, unknown slot
shapes and incomplete required sections; group-owned item containers are kept
even when ownership is implicit. `delete_inactive_players` uses the save's own
real-time clock and a 1-3650 day threshold, removes the profile, owned Pals,
empty guild and newly orphaned containers, and deletes the player file in the
same transaction. If an empty guild owns bases, it also removes the closed
ownership graph of base camps, Palbox/connected structures, workers, work
records, structure item containers, dynamic items and spawners. Retained guild
members, shared reverse references, malformed entities and unknown ownership
fail closed. `transfer_player` accepts only a 32-hex sibling world
ID, binds every byte in both worlds, requires matching validated schemas,
preserves the source unchanged and retains the joined target's UID, instance and
guild. It refuses target-owned world data, ambiguous guild membership, missing
or malformed inventory graphs and every container, dynamic-item or Pal-instance
GUID collision. When several world folders exist, the wrapper selects the live
target from `DedicatedServerName`; it never accepts an arbitrary source path.
Inventory writes require an exact player-save-bound container and slot; item-ID
replacement is refused when the slot carries dynamic metadata, while count-only
edits preserve it. All progression and Pal fields use schema-preserving bounded
property updates and exact post-encode observation.
Do not use the codec's advertised `backup` subcommands: the pinned published
binary omits their required module. See
[`docs/save-repair-lab-validation.md`](docs/save-repair-lab-validation.md).

## Steam build pin and executable rollback

Every maintenance update snapshots the complete executable tree and Steam
appmanifest while excluding `Pal/Saved`. Snapshot metadata records the build,
manifest SHA-256 and critical launcher/server hashes. Set
`PALWORLD_STEAM_BUILD_PIN` to keep unattended maintenance on a known installed
build, or use the Builds panel/`palworldctl builds` to pin a locally retained
snapshot.

Rollback first refuses an unverified or non-local build, online players, and an
incorrect confirmation. `ROLLBACK BUILD` creates a protected save backup,
stops the world, preserves the current executable tree, restores the reviewed
snapshot without replacing `Pal/Saved`, reapplies managed settings, and requires
authenticated REST health before pinning the target. If the older executable
cannot load a newer save format, the prior executable tree is restored and its
REST health is now independently verified. An unhealthy recovery is reported as
incomplete rather than as a successful rollback; the protected save archive and
both executable snapshots remain available for operator recovery.

Pocketpair does not guarantee that newer saves can load in older game versions.
Use a protected save restore only as a separate, explicitly reviewed operation
when losing post-update progress is acceptable. See
[`docs/build-rollback-lab-validation.md`](docs/build-rollback-lab-validation.md)
for the real two-version failure/recovery drill.

## Configuration

All deployment settings live in `/etc/palworld-server.env`. The renderer starts
from the dedicated server's currently shipped `DefaultPalWorldSettings.ini`, so
unspecified game settings remain at Pocketpair's current defaults.

Important variables:

| Variable | Purpose |
| --- | --- |
| `PALWORLD_SERVER_NAME` | Community-browser server name |
| `PALWORLD_SERVER_DESCRIPTION` | Browser description and optional status URL |
| `PALWORLD_PLAYER_EXP_RATE` | Shared global XP multiplier |
| `PALWORLD_BACKUP_LOCAL_ROOT` | Local verified archive directory |
| `PALWORLD_RCLONE_DEST` | Offsite rclone destination |
| `PALWORLD_REQUIRE_LVM_BACKUP` | Refuse backups unless the local path is on LVM |
| `PALWORLD_PUBLIC_DIR` | Directory where public static assets are installed |
| `PALWORLD_EXPECTED_HOSTNAME` | Optional deployment-host safety check |
| `PALWORLD_OPS_TOKEN` | Administrator credential for the private console and API |
| `PALWORLD_OPS_MODERATOR_TOKEN` | Optional announcement/moderation credential |
| `PALWORLD_OPS_VIEWER_TOKEN` | Optional read-only private-console credential |
| `PALWORLD_OPS_INTEGRATION_TOKEN` | Optional independent bearer token restricted to documented redacted read-only integration routes |
| `PALWORLD_ALERT_COMMAND` | Optional executable receiving a health-error string |
| `PALWORLD_AUTO_PAUSE_ENABLED` | Opt in to gracefully stopping an empty server; disabled by default |
| `PALWORLD_AUTO_PAUSE_EMPTY_MINUTES` | Continuous verified zero-player period before auto-pause (5-1440 minutes) |
| `PALWORLD_MODERATION_ENABLED` | Explicit gate for kick/ban controls |
| `PALWORLD_NOTIFY_PLAYER_EVENTS` | Route derived join/leave events through configured notifications |
| `PALWORLD_HEALTH_RECOVERY_ENABLED` | Enable bounded REST/world-stall recovery |
| `PALWORLD_STALL_FAILURE_THRESHOLD` | Consecutive failed/stalled health samples before recovery |
| `PALWORLD_MEMORY_RESTART_GIB` | Optional RSS restart threshold; `0` disables memory recovery |
| `PALWORLD_RECOVERY_*` | Cooldown, time window, and maximum automatic restart controls |
| `PALWORLD_PUBLIC_PROBE_URL` | Optional HTTPS endpoint returning `{"reachable": boolean}` for external UDP testing |
| `PALWORLD_STEAM_BUILD_PIN` | Optional immutable build ID that blocks automatic updates |
| `PALWORLD_BUILD_SNAPSHOT_ROOT` | Save-excluding local executable snapshot directory |
| `PALWORLD_BUILD_SNAPSHOT_RETENTION` | Recent executable builds retained for rollback |
| `PALWORLD_MATRIX_*` | Optional allowlisted Matrix administration bot |
| `PALWORLD_DISCORD_*` | Optional dedicated Discord application, guild/channel boundary, user-role allowlists, and relay destination |
| `PALWORLD_CHAT_RELAY_*` | Optional loopback-only, bearer-authenticated game-chat-to-Discord ingress; global chat is the default category |
| `PALWORLD_SAVE_PARSER_PATH` | Optional local parser override; the embedded source/SHA pin is still enforced |
| `PALWORLD_GAME_DATA_ENABLED` | Opt in to bounded 30-second `/game-data` actor snapshots; unsupported server builds are reported without degrading health |
| `PALWORLD_RCON_ENABLED` | Render and launch private RCON; defaults false and should be changed through the protected console workflow |
| `PALWORLD_RCON_PORT` | RCON listener protected by the required nftables boundary |

## Optional Discord control and chat relay

Use a dedicated Discord application and bot for one server deployment. Install
it into the target guild with the `bot` and `applications.commands` scopes and
only the View Channel and Send Messages permissions needed by the relay. The bot
can delete its own validation message and does not need Manage Messages. Then set
the application, guild, control-channel and user IDs in
`PALWORLD_DISCORD_*`. At least one control channel and administrator are
required; moderator IDs are optional and must not overlap administrators. The
bot registers guild-scoped slash commands at startup and uses only the Discord
Gateway `GUILDS` intent—Message Content intent and an inbound public webhook are
not required.

`/status` and `/players` return redacted operational data. Moderators may use
`/announce` and `/say`; administrators may additionally use `/save`, `/backup`
and `/restart`. Restart requires the exact argument `RESTART PALWORLD`. Every
response is ephemeral, mentions are disabled, identities are allowlisted, and
commands have persistent replay protection, rate limits, bounded output and an
audit record. The Discord application token is separate from notification
webhooks and must remain only in the root-owned environment file.

Game-to-Discord relay is disabled by default. When enabled, the same service
listens only on `127.0.0.1:PALWORLD_CHAT_RELAY_PORT` and accepts:

```http
POST /api/v1/chat
Authorization: Bearer <independent PALWORLD_CHAT_RELAY_TOKEN>
Content-Type: application/json

{"event_id":"unique-event-0001","player":"Display name","message":"hello","category":"global"}
```

The local game/mod adapter must supply a stable unique event ID and POST from the
host. Keep `PALWORLD_CHAT_RELAY_CATEGORIES=global` unless relaying guild or local
say chat is intentional. The token must contain at least 24 characters and must
not be passed in a process command line. Replays are rejected, Discord mentions
are suppressed, message length and throughput are bounded, and chat content is
not written to relay state or audit logs. `/say` provides the reverse
Discord-to-game path through the authenticated private Palworld REST announce
operation. On the opt-in `wine-windows` runtime, the shipped UE4SS hook writes
bounded events to tmpfs and a host adapter forwards them to this ingress without
giving the game process a bearer/Discord credential or persisting chat.

After changing bot settings, run:

```bash
sudo systemctl enable --now palworld-bot.service
sudo systemctl enable --now palworld-game-hook.service # wine-windows chat relay only
sudo systemctl restart palworld-bot.service
journalctl -u palworld-bot.service --since today
journalctl -u palworld-game-hook.service --since today
```

The game-hook service is useful only when `PALWORLD_RUNTIME=wine-windows`,
UE4SS is managed through the Mods panel, and the chat relay plus Discord bot are
configured. It creates the tmpfs spool before a chat event is emitted; the Lua
hook converts the Linux `/run/...` path to Wine's `Z:\\run\\...` view.

Before treating the external integration as proven, create a secret-free plan:

```bash
sudo -u palworld palworldctl discord-validation plan
```

Review its command set, effects and hash, then use that hash with
`palworldctl discord-validation execute --expected-plan-hash HASH --confirm
'VALIDATE DISCORD DELIVERY'`. Nothing contacts Discord during `plan`. Execute
authenticates the bot, verifies that its application, guild and relay channel
match the reviewed configuration, replaces the guild-scoped command set, sends
one mention-suppressed probe to `PALWORLD_DISCORD_CHAT_CHANNEL`, and deletes it
immediately. It writes `/var/lib/palworld/discord-validation.json` without the
token or any Discord identifier and audits success/failure. `status` reads that
receipt without contacting Discord and marks it stale if any bound credential,
identity, channel, role allowlist or command definition changed. Never paste the
bot token into a command or support transcript.

## Mod files and structured configuration

The administrator console manages `.pak`, `.ucas` and `.utoc` files inside the
explicit Palworld Paks root with SHA-bound uploads, preservation and reversible
quarantine. When an installed PalDefender tree contains `Config.json`, the Mods
panel additionally exposes 57 current generated/documented scalar settings grouped by anti-cheat,
protection, administration, chat, announcement, logging and runtime categories.
IP lists, ban data, filters, tokens and unknown future keys never enter the
browser response and are preserved value-for-value by the merge.

Use **Plan mod changes** first. Apply requires `APPLY MOD CONFIG`, the exact
SHA-256 of the reviewed source, the shared mutation lock, a mode-0600 private
snapshot and post-write JSON/hash verification. Rollback requires
`ROLLBACK MOD CONFIG`. No configuration action restarts the world; use a
separate reviewed restart or PalDefender `reloadcfg` after checking the plan.
The same panel discovers installed UE4SS Lua directories and changes their
bounded `mods.txt` state with `SET LUA MOD STATE`.

When private RCON is Ready, the console calls PalDefender `getrconcmds` and
shows only commands actually advertised by the running plugin. Known commands
receive argument/risk guidance; newly discovered unknown commands fail to the
critical risk class. Free-form execution still requires `EXECUTE RCON` and
retains bounded audited output.

This configuration workflow does not make Windows DLL mods executable on the
native Linux Palworld server. Use the separate supported `wine-windows` profile
and hash-pinned lifecycle described in
[`deploy/wine-modded/README.md`](deploy/wine-modded/README.md); DLLs remain
unavailable to the default native process. The profile pins the reviewed
Palworld-specific loader and PalDefender release rather than selecting a moving
latest asset.

When private RCON discovers PalDefender, the Players panel exposes five typed
administrator actions: teleport an online player to bounded X/Y/Z, spawn one
bounded-level wild Pal at bounded X/Y/Z, grant a bounded item quantity, grant
one bounded-level Pal, or grant bounded status points. A missing command
disables only its action; failed discovery disables all five. Plans hash-bind
the target when applicable and all parameters but return only a target hash.
Execution requires `TELEPORT PLAYER`, `SPAWN PAL`, `GRANT ITEM`, `GRANT PAL`,
or `GRANT STATUS POINTS`, then uses the existing private audited RCON history.
Teleport captures position when `getpos` is advertised and fails verification
unless observed X/Y is within 250 units of the reviewed destination. Grants can
prove only command acknowledgement because the plugin has no safe read-back for
inventory, Palbox, spawned actor, or unspent status points.

These controls remain unavailable on the default native-Linux process. The
opt-in Wine profile is live-validated and exposes them only while its running
PalDefender advertises the exact command. Application-level rejection text is
treated as failure rather than acknowledgement. They do not mutate offline
saves. See
[`docs/player-actions-lab-validation.md`](docs/player-actions-lab-validation.md).

## Public status page

Static assets are installed to `PALWORLD_PUBLIC_DIR`. A timer atomically publishes
sanitized `status.json` there every 15 seconds, so public ingress serves files only
and never proxies a request to the operations service:

```caddyfile
handle_path /palworld/* {
    root * /srv/static/palworld
    file_server
}
```

Changes under `public/` are also deployed from `main` by
`.github/workflows/deploy-public.yml`. The repository-scoped `kspls0` runner
publishes atomically with `scripts/deploy-public.sh` and preserves the current
`status.json` while replacing versioned assets.

The map and location dataset come from
[ARXII-13/Palworld-Interactive-Map](https://github.com/ARXII-13/Palworld-Interactive-Map).
Its Apache 2.0 license is retained in `public/MAP-LICENSE.txt`; the underlying
Palworld map artwork remains Pocketpair intellectual property.

## Backups and recovery

Each maintenance run saves and stops the world, archives `Pal/Saved` and the
default settings template, creates a SHA-256 sidecar, opens the archive to
verify it, then uploads both files through rclone. Local archives are retained
for 14 days by default.

Verify an archive manually:

```bash
sudo /usr/local/lib/palworld/verify-backup.sh \
  /var/backups/palworld/palworld-YYYYMMDDTHHMMSSZ.tar.zst
```

Plan a restore first. Execution refuses online players, requires an exact phrase,
makes a protected backup, stages and validates the archive, verifies the restored
world GUID, and automatically restores the prior world tree on failure:

```bash
sudo -E palworldctl restore /var/backups/palworld/palworld-daily-....tar.zst
sudo -E palworldctl restore /var/backups/palworld/palworld-daily-....tar.zst \
  --execute --confirm 'RESTORE WORLD'
```

## Operations and upgrades

`palworldctl` covers status, players, saves, announcements, backups, graceful
restarts, update checks, settings, protected world imports, restores,
diagnostics, preflight, and audit reads.
The administrator browser adds private RCON catalog/free-form execution,
saved/scheduled commands, history, protected RCON enable/disable and staged
whitelist management. Its read-only save view includes searchable offline
profiles/Pals, pinned species work-suitability data, exact assigned-base-worker
joins, 89 bounded named POIs, a copyable planning-coordinate pin, and optional
sanitized live worker/boss/Palbox map layers. The pin never executes a player
action. Game Data is
disabled by default because not every Palworld server build implements the
official optional endpoint. Whitelist enforcement is deliberately disabled until a
non-empty intended membership list is reviewed and exactly confirmed.
The administrator file manager is constrained to config, pak-mod and log roots;
it refuses symlinks/traversal, SHA-binds bounded uploads, preserves replacements,
quarantines removals and never restarts the game automatically.
Short-lived one-use browser pairing codes can be created on the host with
`pairing.py create --role viewer --ttl 300 --confirm 'CREATE PAIRING CODE'`;
only salted hashes are persisted and the resulting session has the usual CSRF,
cookie and expiry controls.
After pulling a toolkit update, `sudo ./scripts/migrate.sh` preserves the environment
and reinstalls code and units without updating the game. To remove the toolkit while
preserving worlds, backups, state, configuration and public assets, run:

```bash
sudo ./scripts/uninstall.sh --confirm-remove-services
```

Maintainers can build a portable source release through GitHub tags or a Debian
package with `./packaging/build-deb.sh VERSION`. The package intentionally stages
the toolkit without silently creating a world; copy and edit the environment, then
run its installed `scripts/install.sh` explicitly.

Keep the operations console behind private ingress. Browser access uses expiring,
CSRF-protected admin/moderator/viewer sessions. Mutations require the independent
administrator token; moderation also requires its explicit feature gate and ban
confirmation. A separate optional integration bearer is limited to the
redacted `/api/v1/integration/*` GET routes documented by
`/api/v1/openapi.json`; it is never an operator credential. See
[`docs/operations-console.md`](docs/operations-console.md).

## Security model

- UDP 8211 is the only game port intended for router forwarding.
- The operations service binds privately; verified nftables rules accept the
  game server's wildcard REST/RCON listeners only through loopback.
- Public status JSON explicitly strips IP addresses and platform identifiers.
- Mutating console actions require a separate operator token.
- The service account receives sudo permission only for packaged exact-command
  lifecycle/recovery wrappers and the read-only REST/RCON firewall check.

See [SECURITY.md](SECURITY.md) for vulnerability reporting guidance.

The pinned Palworld hosting-toolkit and dashboard comparison, exact parity
contract, and ordered implementation queue are in
[`docs/palworld-peer-feature-parity-audit.md`](docs/palworld-peer-feature-parity-audit.md).

## License

Project code is MIT licensed. Bundled map material has separate attribution and
licensing described above.
