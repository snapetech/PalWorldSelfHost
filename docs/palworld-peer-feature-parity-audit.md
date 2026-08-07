# Palworld Hosting Peer Feature-Parity Audit

## Goal

Bring `snapetech/PalWorldSelfHost` to operator-outcome feature parity with the
union of the qualifying Palworld-specific hosting toolkits and dashboards in
this audit. Parity does not require the same operating system, container model,
framework, or visual design. It requires an equally usable operator outcome on
the project's supported Linux deployment.

The goal is complete only when every source-verified row in the feature matrix
is **Parity** or **PalWorldSelfHost exceeds**, with implementation tests and
operator documentation. A feature is not removed from the target merely
because it needs invasive save access, mods, RCON, containers, or a larger
architectural change.

## Scope, discovery, and pins

This is the Palworld peer audit. The sibling Dune Awakening project (DASH) is
the audit-method precedent only and is not a feature target.

The discovery pass searched GitHub repository names, descriptions, README
content, and the `palworld-server` topic for Palworld server Docker images,
installers, managers, panels, dashboards, and toolkits. A project qualifies as
a direct peer when it is Palworld-specific, self-hostable, source-auditable,
and provides meaningful operator outcomes in at least two of installation,
lifecycle, configuration, backup/recovery, monitoring, players, maps, mods, or
administration. A narrowly focused save toolkit can qualify as a companion peer
when its outcomes are used by hosting dashboards.

General-purpose game panels, commercial hosting services, raw install guides,
libraries, standalone config generators, and one-purpose scripts are not direct
parity targets. Superseded and inactive projects are recorded separately so
their distinct features are not lost.

Audit date: **2026-07-15**.

| Repository | Pinned commit | Role in audit |
| --- | --- | --- |
| [`snapetech/PalWorldSelfHost`](https://github.com/snapetech/PalWorldSelfHost) | `912ea4a` before parity work | Baseline; pre-existing dirty worktree changes are not counted as completed parity |
| [`pocketpairjp/palworld-dedicated-server-docker`](https://github.com/pocketpairjp/palworld-dedicated-server-docker) | `70b4dfd2d7f54504882d18c638634b7e8e09c623` | Official container baseline |
| [`thijsvanloef/palworld-server-docker`](https://github.com/thijsvanloef/palworld-server-docker) | `5d4b3a1ab9ed24cb473d603f6666f6a40b9ee2c8` (`2.6.0`) | Direct Linux/container peer |
| [`jammsen/docker-palworld-dedicated-server`](https://github.com/jammsen/docker-palworld-dedicated-server) | `496ae0398d862a6955944f85491c33f1640425d6` | Direct Linux/container peer |
| [`KagurazakaNyaa/palworld-docker`](https://github.com/KagurazakaNyaa/palworld-docker) | `80129da12eaaddd1d2ebc55483be837c85e41125` | Direct lean-container peer |
| [`AlanBacker/PalWorld-Docker-Server-Management-System-Script`](https://github.com/AlanBacker/PalWorld-Docker-Server-Management-System-Script) | `8bfdb59b25256311181566a15517f699e26ba9b5` (`v2.0.1`) | Direct Linux management peer |
| [`zaigie/palworld-server-tool`](https://github.com/zaigie/palworld-server-tool) | `51ee50087165a9ed4930a5d1a1ad4b7ca49f87a8` (`v0.12.2`) | Direct cross-platform web peer |
| [`RNZ01/palworld-server-dashboard`](https://github.com/RNZ01/palworld-server-dashboard) | `7859f8c60d0bab8a845e9fab738bf1f4f5287822` | Direct web-dashboard peer |
| [`8tp/palhelm`](https://github.com/8tp/palhelm) | `e099e8afe4823d6cf6b371e5e3938955e5a1becd` | Direct web-dashboard peer |
| [`io-software-ai/palserver-gui`](https://github.com/io-software-ai/palserver-gui) | `b047192fe0cee5b2e828dc12b2af89d0f1122004` (`v2.2.6`) | Direct Linux/Windows web-management peer |
| [`SSyl/PalworldServerLauncher`](https://github.com/SSyl/PalworldServerLauncher) | `e893646f5620cf452f9e564933dd765103ab6d4d` | Cross-platform-outcome reference; Windows implementation |
| [`mad-001/Palworld-Bridge`](https://github.com/mad-001/Palworld-Bridge) | `fa15bf3fbffd268b6f2801c112d4e58f0a8a4e51` (`v1.7.6`) | Integration/control companion peer |
| [`magicbear/palworld-server-toolkit`](https://github.com/magicbear/palworld-server-toolkit) | `d5cccfe0c6ef200e5b433c7cc37e9cd6986d1199` (`v0.8.5` source version) | Offline save-management companion peer |

Sources reviewed include each pinned README and release metadata, deployment
files, route/controller surfaces, service modules, configuration schemas,
scripts, and tests. Confidence is **high** for source-visible capabilities,
**moderate** for documented runtime behavior that was not deployed against a
live Palworld server, and **unknown** for closed or documentation-only code.

Feature parity is a behavioral target, not permission to copy code. In
particular, `io-software-ai/palserver-gui` is PolyForm Noncommercial,
`SSyl/PalworldServerLauncher` is GPL, and `KagurazakaNyaa/palworld-docker` is
AGPL. Their outcomes must be independently implemented unless a specific reuse
is reviewed as license-compatible. MIT/Apache sources still require their
license and notice obligations when code is reused.

Post-inventory source correction on 2026-07-15: a repository-wide re-search of
all pinned peers found no guild-invitation browser. The earlier matrix wording
had conflated guild data with co-op invite-code import and Discord invite
documentation. It also found the strongest map claim to be named/landmark POIs,
not translated POI labels. Those unsupported adjectives are corrected below;
no source-verified outcome was removed.

## Peer feature inventory

This inventory lists operator-visible outcomes, not every environment variable
or implementation module.

| Peer | Source-verified feature list |
| --- | --- |
| Pocketpair official Docker | Official image and Compose baseline; persistent `Saved`; command-line and INI configuration; manual start, stop, logs, and image-tag update workflow |
| thijsvanloef | x86-64 and ARM64 container; Compose, non-root, Kubernetes and Helm deployment; environment-driven full game configuration; REST and RCON CLIs; save, announcement, moderation, metrics and player commands; manual/cron backups and restore; scheduled updates/reboots with player warnings; auto-pause when empty; Discord lifecycle webhooks; version/manifest locking |
| jammsen | Non-root Docker deployment; environment-driven settings; REST CLI including moderation, metrics, settings and opt-in Game Data actor snapshots; save-aware backup manager and retention; scheduled container tasks; lifecycle webhooks; custom pre-start hook; Helm deployment reference |
| KagurazakaNyaa | Lean Docker deployment; optional SteamCMD validation/update at start; current startup arguments and selected performance settings; REST/RCON configuration; crossplay settings; persistent saves and dedicated pak-mod mount |
| PalDSMS | Doctor and Docker installer; official-image deployment; terminal lifecycle and logs; local-only REST setup; verified backup; hardened restore with pre-restore backup and automatic rollback; update detection with pre-update backup and image-tag rollback; systemd backup/update timers; English, Chinese, and Japanese interfaces |
| zaigie PST | First-run web admin setup; local or remote save-agent mode; responsive desktop/mobile UI; server info, metrics and online players; player, guild, Pal and inventory save views; kick/ban/broadcast/shutdown; visual map; whitelist; custom and scheduled RCON commands; scheduled save synchronization; automatic backups and backup management; browser configuration; Swagger API |
| RNZ01 dashboard | Password-protected web dashboard; server status, uptime, FPS, frame time, world metrics and rolling FPS history; online roster; kick/ban/unban; announcements and common operations; live player map with optional markers; admin and limited-moderator tiers; sampler sidecar; demo and embedded operator docs |
| Palhelm | Admin/viewer web roles; REST, RCON, save-file and optional Game Data integration; FPS/player histories and event feed; online/offline players, guilds, parties, Palboxes, Pal explorer and Paldeck progress; command palette and saved RCON commands; player/base/PalBox map layers; diagnostics; scheduled/manual backups, browse, guarded diff/restore and retention; Compose-aware settings editor; cancellable shutdown countdown; documented control and read-only integration APIs; companion Discord bot contract |
| palserver GUI v2 | Browser-first multi-instance create/adopt/install/control on Linux and Windows; native, custom-Docker and Kubernetes backends; streamed logs and install progress; full typed/raw world settings, Engine.ini tuning and launch arguments; update management; config corruption recovery; online/offline player history, Pals/inventory, guilds and moderation; rich online/offline/base/boss/POI map with coordinate picker; structured RCON console; co-op/dedicated save import and host fix; multi-world and player-save management; scheduled backups/restore/download; UE4SS, PalDefender, Lua/pak mods and file manager; crash/memory/scheduled recovery; token pairing/TLS; sponsor features for Pal stats, teleport, item grants and custom Pals; four languages and responsive themes |
| PalworldServerLauncher | One-click SteamCMD/server install; update-on-start, auto-update and file validation; scheduled restart with warnings/minimum uptime; crash, REST-zombie and stalled-world recovery with loop cutoff; current-save backups on lifecycle/schedule/manual triggers; resource/status tiles, join/leave events and rotating logs; external game-port and accidental REST/RCON exposure check; full settings, difficulty presets, launch arguments, process priority/affinity; REST administration; Discord webhooks and permissioned control bot; four languages |
| Palworld Bridge/Takaro | All official REST endpoints through a remote management adapter; web console; live player tracking and locations; save/shutdown/stop/announce/moderation commands; WebSocket status/events; optional UE4SS chat and connect/disconnect relay between game, Takaro and Discord |
| magicbear toolkit | Offline save parser/editor; list/rename/delete/migrate/copy players; inspect/edit player items, Pals and money; list/delete/move guild membership; delete bases; remove duplicate users and unreferenced containers; repair broken item references; find inactive players; co-op-to-dedicated and cross-world character transfer; Linux CPU-affinity helper |

## Surveyed but not separate parity targets

| Project/category | Treatment | Reason |
| --- | --- | --- |
| `TRRabbit/palworld-server-manager` at `d36d49149e1f4d637e9844829c83ea409de1312e` | Feature-claim reference only | Repository contains user documentation but no application source. Claims include multi-instance, event presets with rollback, whitelist, mod install, live map, setup import/export, Discord controls, and a crash guardian. Source confidence is unknown; overlapping outcomes are already source-verified in other peers. |
| `Dalufishe/palserver-GUI` at `896565d02a86997f61a60ad8df2f9b7e66d6f16d` | Superseded | README identifies `io-software-ai/palserver-gui` as v2; distinct v1 outcomes are covered by v2. |
| `Hoshinonyaruko/palworld-go` at `96c3957905bec2403832d630cae790e68f73d3ac` | Historical reference | Last source activity was 2024. Its web/mobile controls, memory cleanup, scheduled broadcast/restart, RCON, backup, bot, settings, monitoring and injection outcomes are covered by active peers. |
| `ShadeRoller63/Palworld-Dedicated-Server-Docker` at `8dbde7c3d544233f4e5ee78481ad6246165aed79` | Excluded | README instructs users to run thijsvanloef's image, while the only code is an unrelated, nonfunctional camouflage demo. It is not a hosting implementation despite its name and release link. |
| Pterodactyl/Pelican eggs, MCSManager, LinuxGSM, PowerShellGSM, Helm charts | Adjacent ecosystem | General-purpose hosting platforms or packaging recipes. Palworld-specific outcomes are represented by the direct peers; matching the whole generic platform is outside a Palworld peer audit. |
| Forks, translations and repackages of included peers | Covered by upstream | They are not counted twice unless source review shows a distinct operator outcome. |
| Save editors, config generators, RCON libraries, map datasets, watchdog-only scripts | Dependencies or focused tools | They provide one narrow primitive rather than a hosting toolkit/dashboard. Distinct offline save-management outcomes are represented by magicbear. |
| Commercial game-hosting services | Excluded | Not self-hostable or source-auditable. |

## Status definitions

- **Parity**: PalWorldSelfHost provides the same operator outcome, even when the
  implementation or UI differs.
- **PalWorldSelfHost exceeds**: it covers the outcome and adds stronger safety,
  recovery, privacy, evidence, or automation.
- **Partial**: useful overlap exists, but at least one source-verified workflow
  remains unavailable.
- **Gap**: no equivalent operator workflow exists.
- **Architecture gap**: parity requires a deliberate deployment or state-model
  change rather than a small endpoint or screen.

## Union feature matrix

| Area | Peer outcome | PalWorldSelfHost baseline | Status |
| --- | --- | --- | --- |
| Native Linux install | Install SteamCMD/server and service from one workflow | Root installer, locked service account, SteamCMD, systemd and preflight | Parity |
| Official container | Deploy the official or a maintained Palworld image | Compose pins Pocketpair's reviewed official tag and registry digest, persists the complete `Saved` tree, validates and atomically applies a mounted current INI, exposes only UDP 8211, probes the actual child and converts container stop into authenticated REST save/shutdown with process-group fallback; the exact image completed a clean start, persisted restart and 12-second exit-0 stop lab | Parity |
| Kubernetes/Helm | Run with persistent saves and current configuration in Kubernetes | Helm chart renders a single-replica StatefulSet, RWO PVC, external settings Secret, private REST boundary, probes and graceful adapter; an isolated kind 1.35 lab proved the exact pinned image Ready, authenticated REST identity, an active world, 5-second graceful pod deletion through REST save/shutdown, and a replacement Ready with the same PVC, PV and world GUID | Parity |
| Browser bootstrap | Create/adopt/install a server and credentials from a first-run UI | One-shot loopback UI unlocks with an ephemeral terminal code, plans fresh SteamCMD install or exact-fingerprint adoption, checks paths/ports/prerequisites, generates independent credentials in-browser, exact-confirms execution, streams redacted bounded progress and records a secret-free receipt; an isolated Ubuntu 24.04/systemd lab completed real HTTP adoption, service/timer installation and hash-verified idempotent resume | Parity |
| Multi-instance | Create and independently operate multiple worlds/ports | Supported official-image Compose manager creates collision-checked per-instance game/query/loopback REST/RCON ports, persistent world ID, settings, save tree and project identity. Start/stop/restart/status are project-scoped; exact-confirmed deletion performs graceful `down` without volumes and retires the complete save tree. Two real concurrent PalServer instances proved distinct GUIDs, stopping one while the other remained healthy, same-GUID restart and preserved save markers | Parity |
| Existing-install adoption | Attach management to an existing server tree without reinstall | Reviewed-fingerprint adoption verifies native Linux executables/Steam manifest/world inventory, refuses symlinked/changing trees and live unmanaged processes, preserves the active INI as the managed raw base, records a receipt, transfers ownership to the locked service account and installs services without running SteamCMD | Parity |
| World/save import | Import dedicated or co-op worlds and select the active world | CLI plan/import accepts bounded dedicated or co-op world directories, binds execution to every source-file hash and an explicit target, requires zero players, creates a verified protected backup, preserves replaced targets, selects the imported world and automatically restores the prior world/selection if REST startup verification fails | Parity |
| Lifecycle control | Start, stop, restart, save and show state | systemd, CLI, REST client and guarded browser actions | Parity |
| Live logs | Stream/filter game and manager logs in the browser | Private live journal polls every five seconds with allowlisted units, bounded line/time windows, literal filtering and redaction | Parity |
| Crash recovery | Restart dead processes and stop crash loops | systemd `Restart=on-failure` with start-limit guard and health alerting | Parity |
| Zombie/stall recovery | Restart a live process when REST or world time stops responding | Persisted REST/uptime failure streak triggers forced systemd recovery under the mutation lock, with cooldown/window limits, audit and notifications | Parity |
| Memory recovery | Restart at a configurable memory threshold with loop protection | Configurable RSS threshold uses player-aware deferral, graceful save/restart, mutation exclusion, cooldown and maximum-recovery window | Parity |
| Auto-pause | Suspend an empty server and resume on demand/activity | Opt-in minute evaluation requires a continuously verified zero roster for 5-1440 minutes, fails open and resets on roster errors, rechecks under the shared mutation lock, saves and gracefully stops, and records/audits paused state; administrators can resume on demand from the guarded browser/CLI path with a fresh idle window | Parity |
| Full game settings | Edit all current shipped settings with types | Dynamically reads all defaults and supports typed plan/apply/rollback; curated metadata is less extensive than the strongest peer UI but does not reduce setting coverage | Parity |
| Settings guidance | Categories, descriptions, ranges, search, presets and effective/pending state | Installed-build discovery is enriched with the current peer catalog's categories, choices, advisory ranges/units, string limits and operator-specific descriptions; semantic type/name rules cover newly shipped keys without generic placeholders, while search, staged presets, changed-value highlighting and rendered/restart-pending state complete the workflow | Parity |
| Raw configuration | Guarded raw INI edit that preserves unknown fields/comments | Administrator raw view/plan/apply/rollback preserves unknowns/comments, masks and server-preserves protected values, caps input, redacts diffs, atomically renders and automatically restores on failure | PalWorldSelfHost exceeds |
| Engine tuning | Typed/raw Engine.ini performance and network controls | Administrator typed and raw Engine.ini plan/apply/rollback covers network, frame-rate and garbage-collection controls with documented bounds and presets; unknown keys/comments survive, managed values are authoritative and reapply before every start | PalWorldSelfHost exceeds |
| Launch arguments | Edit and preview supported server launch arguments | Typed browser/API controls validate and preview listing, log-format, worker-thread and legacy performance flags; `start.sh` consumes the authoritative saved arguments and retains managed port/address arguments | Parity |
| Config recovery | Detect malformed/conflicting INI/WorldOption/env config and restore a known-good version | Browser/CLI health classifies PalWorldSettings.ini and Engine.ini structure, duplicate/missing environment keys, invalid/conflicting ports, and co-op WorldOptions overrides; exact-confirmed recovery gracefully stops the world, preserves corrupt/conflicting files, regenerates from authoritative state, rolls preservation back if regeneration fails, and retains a validated-install environment snapshot for root recovery | PalWorldSelfHost exceeds |
| Update detection | Compare installed and current Steam builds | Thirty-minute detection timer and persisted update state | Parity |
| Safe game updates | Warn/defer for players, save/backup, update, validate health, roll back bad build | Automatic/browser paths warn, save, require verified protected backup, snapshot executables, bind the reviewed target, update and health-check; Steam/render/post-health failure restores and pins the prior executable tree without touching saves; live zero-player protected Steam validation/restart proof completed on build `24181105` | Parity |
| Version pin/downgrade | Lock a known manifest or roll back unattended | Installed/local-snapshot builds can be pinned, listed, planned and exact-confirmed from CLI/browser; authentic two-version executable restores verify manifest/critical hashes without overwriting saves, and real server-process validation proved forward persistence plus fail-back to a separately REST-verified prior build when Pocketpair rejected a newer save on the older version | Parity |
| Backup creation | Manual and scheduled save-aware backups with rotation | Hourly/daily/weekly/protected tiers, SHA-256, archive validation and manifests | PalWorldSelfHost exceeds |
| Offsite backup | Copy immutable verified artifacts away from the host | rclone copy of archive, checksum and manifest with local retention on failure | PalWorldSelfHost exceeds |
| Backup browser | List, inspect, download and delete/quarantine snapshots in UI | Role-aware inventory, manifest/checksum state, download, on-demand verification and reversible quarantine | Parity |
| Restore | Preview, verify, stop world, pre-backup, restore and recover on failure | CLI and browser plan/execute, structural/checksum validation, player refusal, protected pre-backup, staging, GUID check and automatic rollback | PalWorldSelfHost exceeds |
| Restore drills | Prove backups restore without touching production | Weekly isolated restore drill | PalWorldSelfHost exceeds |
| Online players | Current roster and server metrics | Private roster/moderation plus sanitized public roster/map | Parity |
| Player history | Offline roster, first/last seen, sessions and join/leave timeline | Searchable private browser/API roster exposes online state, first/last seen, session count and retained duration, with platform identity restricted to moderator/admin; SQLite retains the underlying 90-day join/leave timeline shown in the redacted event feed | Parity |
| Moderation | Kick, ban, unban and announcements | Explicitly gated REST moderation and announcements with audit | Parity |
| Whitelist | Manage an enforced allow-list | Transactional identity allow-list with plan/replace, empty-list and duplicate refusal, exact-confirmed enable/disable, 60-second grace, 15-second live-roster enforcement, hashed rejection audit and fail-safe roster-error behavior | PalWorldSelfHost exceeds |
| Admin console | Structured/free-form RCON commands, history and saved commands | Private browser/CLI Source-RCON console with official command catalog, validated free-form input, SQLite actor/source/result/output history, saved commands and exact execution confirmation; REST/RCON are fail-closed behind verified loopback-only nftables rules | PalWorldSelfHost exceeds |
| Scheduled commands | Schedule arbitrary/saved administrative commands | One-shot or recurring validated free-form/saved RCON jobs retain scheduler-attributed result/output history and require exact creation confirmation | Parity |
| Save-derived players | Inspect offline player profile, progression, inventory and Pals | Private console/API profiles combine player-save and Level-save data for names, progression, last position, item stacks, Paldeck progress and owned Pals; viewer identities are redacted, parsing is record-streamed from a temporary copy, and the external codec is source/SHA pinned | Parity |
| Guild/base data | Browse guild membership, bases and workers | Build-verified save scans expose guild names/levels/members/admin scope, named base locations and exact assigned-worker joins; live build `24181105` proved 2/2 worker-container decodes and 4 exact Pal assignments | Parity |
| Pal data | Search parties, Palboxes, ownership, levels, traits and work suitability | Server-wide owner/Pal/work search covers party, Palbox and base placement, species/name, owner, level, rank, gender, lucky state, talents, passives and commit/SHA-pinned species work-suitability levels | Parity |
| Live Game Data | Poll bounded actor snapshots for richer live state | Opt-in 30-second poller bounds responses/actors, refuses redirects, drops IP/user/raw actor identifiers, validates finite metrics/coordinates, protects last-good state from one-sample collapse and exposes ready/stale/unsupported/unauthorized/unavailable independently of ordinary REST health | Parity |
| Player map | Plot online players on Palpagos map | Public live coordinates plus selected POIs | Parity |
| Rich map layers | Offline last position, bases/workers, bosses, Palboxes and named POIs | Private layers combine offline last positions, guild bases, assigned-worker counts, placed structures and 89 bounded named POIs with optional fresh Game Data worker, wild-boss and Palbox markers; endpoint capability state is explicit | Parity |
| Coordinate actions | Pick a map point for teleport/spawn workflows | Private coordinate clicks feed hash-bound typed teleport or bounded-level wild-Pal spawn plans only when live PalDefender discovery advertises the exact `tp` or `spawnpal` command. Execution requires action-specific exact confirmation; teleport captures available before/after position and fails when observed X/Y misses the target, while spawn honestly reports command acknowledgement. Current Palworld/PalDefender under the supported opt-in Wine profile advertised both commands; application-level rejection fails closed, and success/observation paths have fixture/browser proof | Parity |
| Mod lifecycle | Install/update/remove UE4SS, PalDefender, Lua and pak mods | SHA-reviewed `.pak`/`.ucas`/`.utoc` bundles can be installed/replaced with prior-version preservation and reversibly quarantined; UE4SS Lua state is exactly confirmed and audited. The supported opt-in Wine profile adds catalog-pinned, archive/hash-verified PalDefender and Palworld-specific UE4SS install/update/remove/rollback with drift/running-server refusal and config/operator-mod preservation. A real current server loaded both, survived removal/rollback and reloaded the same world | Parity |
| Mod configuration | Structured PalDefender/mod settings and command discovery | Admin-only browser/API exposes all 57 scalars in PalDefender 1.8.3's live-generated config plus bounded MOTD, preserves unowned/identifier-bearing keys, SHA-binds plan/apply, snapshots privately, verifies/rolls back writes, and never auto-restarts; private RCON parses the current live catalog and conservatively classifies unknowns. Real apply/reload/semantic-diff/rollback proof completed on the supported Wine backend | Parity |
| File manager | Guarded browse/upload/edit/delete within server roots | Administrator browser/API/CLI browse three explicit roots, UTF-8 edit/read, SHA-bound uploads up to 64 MiB, preserved replacements and reversible quarantine; parent traversal, symlinks, special files, unlisted extensions and automatic restarts are refused | PalWorldSelfHost exceeds |
| Save migration/repair | Host fix, character transfer, duplicate/broken-reference cleanup | Admin-only diagnostics decode Level plus every player save and expose identity plus broken/unreferenced graph evidence. Rename, unused-UID migration, fail-closed joined-identity replacement, duplicate cleanup, complete-reference graph cleanup, save-clock-based inactive-profile deletion and read-only sibling-world player transfer all use the build/schema/parser/all-byte-bound protected transaction. Transfer copies progression, inventory, party and Palbox graphs into a disposable joined target while retaining its target-world identity/guild, and refuses malformed, ambiguous or colliding graphs. Inactive cleanup removes the profile, owned Pals and empty guild; for base-owning empty guilds it deletes a closed reverse-reference-verified graph of bases, workers, work records, Palbox/connected structures, item containers, dynamic items and spawners, while retained members or shared/unknown ownership fail closed. A read-only production-derived build `24181105` clone exposed and fixed the current nested guild-name schema; protected rename/apply preserved decoded structural equality and the exact world reopened in Pocketpair's official image, retained the mutation and exited cleanly | Parity |
| Save/player mutations | Teleport, grant items/Pals, Pal stats and selected offline edits | Admin-only typed teleport, coordinate wild-Pal spawn and bounded item/Pal/status grants are live-capability-discovered, plan-hash-bound, action-confirmed and privately audited; teleport verifies its observed destination while spawn/grants expose only application-level command acknowledgement. Current Palworld/PalDefender under the supported Wine profile advertised all five action commands and rejection fails closed. The protected save-write transaction supports player rename/unused-UID migration, exact player-save-bound inventory slots including `Money` currency, player level/experience, and owned-Pal nickname/level/condensation rank/HP-attack-defense talents/passives. Dynamic item metadata is preserved for count-only edits and blocks item-ID replacement. The current-build schema/codec write path and official-server reopen are process-proven | Parity |
| Temporary events | Apply timed XP/drop/capture presets and automatically restore prior settings | Exact-confirmed Double XP, Resource Rush and Capture Weekend jobs snapshot only affected overrides, restart into the event, restore them automatically after a bounded duration, preserve unrelated intervening changes, retry failed restoration, and restore early when cancelled | PalWorldSelfHost exceeds |
| Metrics history | Retain and chart FPS, frame time, players, uptime, RSS and restarts | Retains 90 days in SQLite and exposes a bounded/downsampled API plus private FPS/player chart and RSS/restart summaries | Parity |
| Event feed | Show joins/leaves, lifecycle, maintenance and moderation events | Redacted browser audit timeline includes derived join/leave presence plus lifecycle, maintenance, health, settings, backup, restore, scheduler and moderation actions | Parity |
| Diagnostics | Channel health, backup freshness, host resources and redacted support data | CLI health/preflight plus browser world, REST, health, backup, storage, update and maintenance signals; redacted support bundles are generated, administrator-streamed and immediately removed | PalWorldSelfHost exceeds |
| Exposure check | Test public game reachability and warn if REST/RCON are internet-exposed | Hourly/on-demand IPv4/IPv6 listener audit verifies loopback operations plus fail-closed nftables protection for Palworld's wildcard REST/RCON listeners, and supports a bounded HTTPS external UDP probe contract for public reachability | Parity |
| Authentication/roles | Admin, moderator/viewer, protected sessions and remote pairing | Admin/moderator/viewer tokens, throttled expiring sessions, HttpOnly/SameSite cookies, CSRF and legacy role headers plus host-created 12-character one-use role pairing codes stored only as salted hashes, bounded to 60-600 seconds and atomically consumed into the same protected sessions | Parity |
| API/integrations | Documented control API plus separate redacted read-only integration keys | Documented versioned control API plus an independent bearer credential accepted only by three redacted/aggregate GET routes; its public OpenAPI 3.1 contract describes status, bounded events and aggregate Game Data, and the bearer is refused by ordinary viewer and every mutation route | Parity |
| Public status | Safely publish health, player count/history and map without admin proxy | Atomic static, sanitized status site | PalWorldSelfHost exceeds |
| Notifications | Route lifecycle/update/health/player events to webhooks | Generic, Discord, ntfy and Gotify routes cover lifecycle/update/health and optional derived join/leave events | Parity |
| Chat/control bot | Permissioned Discord or equivalent commands and optional chat relay | Guild-scoped Discord slash commands provide allowlisted viewer/moderator/admin status, roster, announce, save, backup and exact-confirmed restart operations with ephemeral responses, replay/rate protection, redaction and audit; `/say` relays to game. The supported Wine/UE4SS hook registered in current Palworld and writes token-free bounded events to tmpfs; the hardened host adapter's bearer-authenticated loopback forwarding/retry/filter path is process-proven without persisting chat. An exact-confirmed external validator binds secret hashes and boundaries, verifies bot/application/guild/relay-channel identity and the seven-command response, then sends and deletes one mention-suppressed probe while recording an identifier-free receipt. Fixture/process validation is complete, but that transaction has not run against an explicitly authorized live Discord guild | Partial |
| Audit | Record state-changing operations and failures | Append-only JSONL audit across core workflows | PalWorldSelfHost exceeds |
| Mobile/responsive UI | Operate from phone/tablet | Public and role-aware private consoles have responsive layouts verified at 390px | Parity |
| Localization | Operate UI/CLI in multiple languages | English, Simplified Chinese and Japanese cover every static private-console operator string, placeholder and accessibility label plus core dynamic health/action states; locale is browser-detected, explicitly selectable, locally persisted and directly linkable, while `palworldctl` accepts the same locale contract and localized command aliases; stable API keys/routes and exact safety tokens remain English | Parity |
| ARM64 | Run through a supported ARM64 image/emulation path | Separate server-only Compose path pins qualifying peer release `v2.6.0` by OCI index and ARM64-manifest digest, persists `/palworld`, exposes only UDP 8211, keeps REST private, disables RCON and applies conservative Box64 flags. Preflight refuses non-native ARM64 Docker, non-4-KiB pages, weak/permissive credentials, unknown device profiles and digest drift. The pulled artifact's AArch64 Box64 successfully executed its x86-64 SteamCMD through a nested emulator lab, with native ARM64 CI retaining the same proof | Parity |

Current implementation snapshot: **59 rows — 0 Architecture gaps, 1 Partial,
44 Parity, and 14 PalWorldSelfHost exceeds.** The goal remains active
until all 59 rows are Parity or Exceeds under the mitigation contract below.

## Implemented parity tranches

### Role-aware operations foundation

Added in the first Palworld peer-parity tranche:

- versioned `/api/v1` routes with admin, moderator and viewer authorization;
- throttled token-to-session login, expiring in-memory sessions, HttpOnly and
  SameSite cookies, CSRF enforcement, Origin/Host checks and security headers;
- backward-compatible role-aware header authentication;
- bounded/redacted allowlisted systemd journal access;
- bounded/downsampled retained metrics API and selectable multi-metric private charts;
- literal-filtered, time/line-bounded live journal polling;
- complete browser channel diagnostics and ephemeral redacted support download;
- redacted browser event feed;
- private first/last-seen/session history and derived join/leave audit events;
- optional join/leave notification routing without persisting network addresses;
- consecutive REST/world-uptime stall recovery and player-aware RSS recovery;
- persisted cooldown/window guards preventing automatic restart loops;
- scheduled/on-demand control-plane listener audit and external public-game probe;
- browser Steam check/plan/exact-execute bound to the reviewed target build;
- pre-update executable snapshots with appmanifest evidence and critical hashes;
- automatic executable-tree rollback for Steam, render and post-start health failure;
- exact-confirmed browser/CLI build pin, unpin, plan and rollback preserving saves;
- backup list/detail/download/verify/reversible-quarantine API and UI;
- guarded browser restore planning/execution backed by structural archive
  validation and the existing rollback transaction;
- constrained archive path resolution and audited administrator downloads;
- searchable settings presentation that submits changed values only; and
- categorized metadata, staged presets and rendered/restart-pending state;
- guarded raw INI plan/apply/rollback with protected-value preservation; and
- typed/raw Engine.ini tuning with advisory ranges, presets, unknown-field
  preservation, restart reapplication and transactional rollback;
- editable/previewable typed launch arguments consumed by `start.sh`; and
- bounded timed XP/resource/capture events with affected-key snapshots,
  automatic/cancel restoration and failed-restore retry; and
- configuration health/recovery for malformed world/Engine INI, environment
  syntax/port conflicts and co-op WorldOptions overrides; and
- responsive desktop/mobile operational hierarchy and signal-rail navigation.

The integration surface also has a dedicated bearer credential that cannot be
used for browser sessions or operator routes, three redacted/aggregate GET
routes and a public OpenAPI 3.1 description. Tests prove the bearer is refused
by both ordinary viewer reads and mutations.

Role-scoped remote pairing uses host-generated 12-character one-use codes with
60-600 second expiry. Only salted hashes are stored; consumption is atomic and
creates the same expiring HttpOnly/SameSite/CSRF-protected session as token
login. Pairing attempts share the five-minute login throttle.

Existing native-Linux trees have a separate adoption transaction. It hashes the
launcher, server executable, defaults, active settings and Steam manifest;
inventories bounded world/player counts; refuses symlinked, overlapping or
changing trees and live unmanaged processes; preserves the active settings as
the managed raw base; and binds installation to an exact reviewed fingerprint.
The installer then transfers the tree to the locked service account and skips
both SteamCMD and pre-handoff rendering. A read-only `kspls0` plan on 2026-07-15
resolved build `24181105` and one world and correctly reported
`blocked-running` while the production server remained active.

First-run browser provisioning wraps the same fresh installer and adoption
transaction in a one-shot loopback service. An ephemeral terminal code unlocks
the UI; managed paths and ports are bounded; host prerequisites are explicit;
and a short-lived server-held plan binds mode, normalized configuration and any
adoption fingerprint. Credentials are generated in the browser and submitted
only with exact confirmation. The environment is atomic, existing configuration
is never overwritten, progress is bounded and credential-redacted, and success
records a secret-free receipt. Failed execution can retry only the same plan and
credential pair; completed adoption resumes only after critical hashes reverify.
An isolated Ubuntu 24.04/systemd lab completed the real HTTP adoption and unit
installation twice without reinstalling game files. See
[`bootstrap-lab-validation.md`](bootstrap-lab-validation.md).

World import is a separate offline mutation for a directly selected Palworld
world folder. It accepts only the bounded root save files and GUID-named player
saves, fingerprints every byte, requires an explicit target when selection is
ambiguous, refuses managed-tree sources, requires a zero roster, and preserves
the source. Execution forces a save and verified protected backup, stops the
service, atomically selects the staged world, verifies REST after restart and
restores both the prior target and `GameUserSettings.ini` on failure. A
synthetic co-op-layout plan was verified read-only against `kspls0` while the
production game and operations services remained active; production execution
was intentionally not attempted against the live community world.

Auto-pause is disabled by default. When enabled, a minute timer requires 5-1440
continuous minutes of successful zero-player rosters, resets its countdown on
players or any roster failure, and rechecks under the shared mutation lock
before a save and graceful stop. The private console exposes guarded on-demand
start/resume and exact-confirmed graceful stop; resume creates a fresh empty
window. Unit tests cover disabled, unavailable-roster, countdown, pause-due and
resume state transitions without stopping production.
The default-disabled timer and authenticated status projection were deployed and
verified on `kspls0` on 2026-07-15; production remained active and no pause was
attempted.

Localization uses complete static catalogs for English fallback, Simplified
Chinese and Japanese, including placeholders and accessibility labels, with
mutation-aware translation of dynamically inserted operational labels. Locale
selection follows an explicit query, saved browser preference, browser language,
then English; only the non-sensitive locale is stored. `palworldctl` supports
the same explicit locale set and translated aliases/help while keeping API/JSON
and exact confirmation contracts stable. Automated catalog coverage and CLI
tests pass; headless Chromium render review covered Chinese at 1440px and
Japanese at 390px with persisted second navigation. The preferred in-app and
isolated-workspace browser runtimes were unavailable for this review.
The 237-entry Chinese and Japanese catalogs, rendered shell, saved Chinese
preference and localized CLI help were then verified through a loopback tunnel
to `kspls0`; the static/CLI-only deployment required no service restart and the
game, operations and auto-pause timer remained active.

The portability adapter uses Pocketpair's official `amd64` image at tag
`v1.0.1.100619` and manifest digest
`sha256:0d293cafd503a91a6d11d71f7bf770ee0c3c5ecf37db988349b2c1758f4e9358`.
An isolated Docker lab proved the actual image reached game version
`v1.0.1.100619`, served authenticated REST, retained the supplied server name
and generated world across a second start with no settings mount, saved through
REST, accepted REST shutdown and exited 0 in 12 seconds without an OOM kill.
The adapter also failed closed on Pocketpair's non-executable launcher until it
was corrected to invoke the readable script through `/bin/sh`, and a signal-only
trial correctly remained non-passing rather than being counted as graceful.
Compose validation, shellcheck and failure/stop fixture tests pass. The Helm
chart lints and renders both generated and externally managed Secret/PVC modes.
An isolated kind 1.35 lab ran the exact pinned image Ready with authenticated
REST identity and an active world on a bound 20 GiB RWO PVC. Deleting the pod
completed in 5 seconds after successful REST save/shutdown; its replacement
became Ready with zero restarts, the same PVC/PV identity and world GUID, and the
exact image digest. Kubernetes therefore meets the row's parity contract. See
[`portability-lab-validation.md`](portability-lab-validation.md).

The constrained file manager exposes only configuration, pak-mod and log roots.
It refuses parent traversal, symlink components, special files and unlisted
extensions; caps UTF-8 text at 1 MiB and raw uploads at 64 MiB; SHA-binds every
upload to the reviewed body; serializes writes; preserves replacements; moves
removals to reversible quarantine; and never restarts the game implicitly.
On 2026-07-15, `kspls0` proved CLI upload/read, authenticated administrator API
read and reversible quarantine with a disposable Markdown file while the game,
operations, maintenance and Game Data units remained active.

Live update evidence on 2026-07-15: with zero players online, `kspls0` created
and re-verified a protected backup, stopped gracefully, snapshotted executable
build `24181105`, forced SteamCMD through validation of 5,154,554,668 bytes,
re-rendered configuration, restarted, and passed REST/build/exposure checks.
The maintenance timer was restored and all game/control timers remained active.
An anonymous Steam `app_info_print 2394010` check on the same date exposed only
the current public build `24181105` and no previous-version branch. On
2026-07-16, immutable official Pocketpair images then supplied authentic builds
`v1.0.0.100427` and `v1.0.1.100619` for an isolated two-version drill. Their
server/pak hashes differed; executable snapshot/restore round trips preserved a
save sentinel. The older process created a world, the newer process reopened the
same GUID, and the older process then correctly failed on Pocketpair's newer-save
format guard. Restoring the newer image recovered authenticated REST with the
same GUID. The failure exposed and corrected a missing REST verification after
automatic executable recovery. See
[`build-rollback-lab-validation.md`](build-rollback-lab-validation.md).

Evidence:

- [`operations-console.md`](operations-console.md)
- `tests/test_ops_server.py`
- `python3 -m unittest discover -s tests -v`
- authenticated 1440px and 390px local Chromium render review

### Build-pinned save intelligence

Added in the save-intelligence tranche:

- a read-only scan transaction that requests a live flush, copies active save
  data to a private temporary directory, and removes decoded intermediates;
- a separately executed `palsav-flex` codec pinned to upstream commit
  `2c8c65c4a60b04e63eeb7f0c1857a5ba903a24d9` and Linux binary SHA-256
  `f9a7e17782c233ba1be286ce3239447046024c76e2b5f5b375cb25f00743948e`;
- record-bounded streaming analysis rather than loading multi-gigabyte decoded
  worlds into application memory;
- explicit compatible/degraded schema state, required-section drift and bounded
  parser errors tied to game build, save hash and parser provenance;
- offline player progression, last coordinates, categorized inventory, owned
  party/Palbox Pals, traits and Paldeck progress;
- exact joins from Pal container IDs to decoded base worker-container IDs;
- a commit/SHA-pinned 809-species public metadata snapshot for work-suitability
  levels, with boss-variant normalization and searchable owner/Pal/work views;
- guild membership/level/admin scope, guild-linked bases and placed structures;
- viewer-safe identifier redaction, moderator/admin identity visibility, a
  scheduled 30-minute refresh and administrator on-demand scanning; and
- an opt-in 30-second Game Data poller with a 32 MiB/250,000-actor input bound,
  a 2,048-actor sanitized projection, redirect refusal, finite-value checks,
  population-collapse confirmation and explicit capability/freshness states;
- IP, userid, raw actor/trainer IDs and raw action/class strings are never
  persisted by that poller; and
- responsive private profile/guild browsers plus offline-player, base,
  structure, 89 named-POI and optional live worker/boss/Palbox coordinate
  layers; the same bounded field provides a copyable, non-executing planning pin.

Live evidence on 2026-07-15: production build `24181105` decoded successfully
in 0.04 seconds from a read-only 75,939-byte `Level.sav` copy, producing a
5,188,898-byte JSON stream. The independent analyzer reported compatible
schema with 2 players, 32 Pals, 2 guilds, 2 bases, 4 exactly joined base workers
and 64 placed objects. Twenty-eight owned Pals resolved pinned work-suitability
metadata; both player saves supplied inventory, Paldeck and last-position
linkage. The live server returned HTTP 404 for `/v1/api/game-data`, which is now
reported as `unsupported` while ordinary REST, exposure and game health remain
healthy. The scan never writes a Palworld save.

Evidence:

- `scripts/save-intelligence.py`, `scripts/game-data-poller.py`
- `systemd/palworld-game-data.service`, `systemd/palworld-game-data.timer`
- `tests/test_game_data.py`
- `tests/test_save_intelligence.py`
- `tests/test_ops_server.py`
- live production read-only compatibility scan on `kspls0`

### Transactional offline rename and UID migration

The first writable save-repair tranche deliberately rejects the pinned codec's
broken backup/import commands and uses only its verified SAV-to-JSON and
JSON-to-SAV core. A repair plan binds the complete selected world inventory,
installed Steam build, parser SHA and schema signature. Rename updates the exact
player plus matching guild membership; UID migration replaces whole GUID values
in Level and player saves and refuses an existing target identity or file.
Existing-identity replacement removes a joined placeholder, reuses its instance
ID, removes only an empty placeholder guild and fails closed if target-owned
references remain. Duplicate cleanup selects a canonical character only when
player-save and guild evidence agree, removes stale character/guild handles and
fails closed if the stale instance remains referenced elsewhere. Save-graph
cleanup scans Level plus every player save, repairs dangling character and
dynamic-item slots, removes only globally unreferenced containers/items, keeps
implicit valid-guild item ownership and refuses duplicate IDs or unknown shapes.

Execution requires an exact operation phrase, rechecks a verified zero roster,
flushes and re-fingerprints the save, creates a protected backup, stops the
world, stages the entire world directory, and demands decoded structural
equality for every encoded save. The staged directory is atomically selected
and must pass REST startup. Any failure restores the untouched directory and
attempts to restart the prior world; success retains that directory privately.

The transaction and browser workflow are fixture-proven, including startup
rollback. A read-only production-derived build `24181105` clone then exposed
and fixed the current nested guild-name shape; the protected staged rename
retained decoded structural equality, and Pocketpair's exact official image
reopened the same world GUID, retained the mutation and stopped cleanly. Host
replacement, cross-world transfer, duplicate cleanup and base-owning deletion
remain direct success/refusal fixture proofs because the snapshot contained no
disposable identity graph, but the operator outcomes and current write/process
contract now satisfy the row's parity boundary. See
[`save-repair-lab-validation.md`](save-repair-lab-validation.md).

Evidence:

- `scripts/save-repair.py`, `scripts/ops-save-repair.sh`
- `tests/test_save_repair.py`, `tests/test_ops_server.py`
- real pinned-codec round trip, authenticated 1440px/390px local Chromium
  validation, and a read-only production-derived current-build clone mutated
  and reopened in Pocketpair's official image without changing production

### Private RCON, saved/scheduled commands and whitelist

Added in the console/player-administration tranche:

- a standard-library Source-RCON client restricted to loopback, including the
  live-observed Palworld response-ID behavior, one-line/512-byte bounds and an
  explicit ban on credential-setting commands;
- a root-owned nftables service required before the game starts, accepting
  REST/RCON only on loopback and dropping those wildcard Palworld listeners on
  every other interface;
- zero-player, exact-confirmed enable/disable transitions that take and verify a
  protected backup, restart, prove REST/RCON health and restore the environment
  plus service automatically on any failed check;
- an administrator-only browser/CLI catalog and free-form console with bounded
  SQLite actor/source/command/result/output history and saved commands;
- exact-confirmed one-shot or recurring direct/saved RCON scheduler work;
- a transactional identity allow-list with plan/replace, duplicate/empty-list
  refusal, exact enable/disable, 60-second grace and 15-second kick enforcement;
  roster failures cause no kick attempt and audit identities only by hash; and
- a responsive two-column/one-column console and staged allow-list editor.

Live evidence on 2026-07-15: production RCON was enabled only after zero-player
proof and a new verified protected backup. Palworld build `24181105` returned
authenticated `Info`/`ShowPlayers` over loopback; TCP 25575 and REST TCP 8212
were both blocked from `kspld0` while loopback remained healthy. The live
exposure report was safe with no issues. An audited saved `Info` command ran,
and an exact-confirmed one-shot `Info` job created through the production admin
API completed through `palworld-scheduler.service` with result `0` and
scheduler-attributed console history. The whitelist timer and dry-run path were
healthy, but production enforcement remains deliberately disabled until the
operator supplies the intended membership list. Two deliberately failed RCON
health attempts automatically restored the disabled environment; the preserved
evidence exposed and corrected Palworld's response-ID compatibility behavior.

Evidence:

- `scripts/rcon-client.py`, `scripts/rcon-firewall.py`, `scripts/rcon-console.py`
- `scripts/ops-rcon-toggle.sh`, `scripts/whitelist-manager.py`,
  `scripts/whitelist-enforcer.py`, `scripts/scheduler.py`
- `tests/test_rcon.py`, `tests/test_whitelist.py`, `tests/test_scheduler.py`,
  `tests/test_exposure.py`, `tests/test_ops_server.py`
- live protected transition, rollback, external-boundary and scheduler proof on
  `kspls0`; authenticated 1440px and 390px Chromium render review

## Exact-parity mitigation contract

No source-verified capability in the matrix is excluded from the target. The
implementation must preserve these controls:

1. **Save parsing and writes.** Pin parsers to a game build/schema; detect drift;
   stop the world for writes; take and verify a protected backup; offer dry-run
   diffs; stage writes; verify the result; and automatically restore on failure.
2. **Mods and remote artifacts.** Pin provenance and immutable hashes; display
   permissions; stage extraction; reject path traversal/links; back up replaced
   files; support rollback; and isolate processes/network access where possible.
3. **RCON and arbitrary commands.** Keep RCON private; separate read and write
   permissions; classify commands; bound input/output/time; require confirmation
   for destructive commands; and audit requester, target, command class and result.
4. **Multi-instance.** Replace global paths, locks, state, ports, units and public
   snapshots with instance-scoped identities before exposing create/delete. An
   instance deletion must preserve saves by default.
5. **Browser provisioning and file access.** Constrain all paths to explicit
   roots; plan before apply; redact secrets; use atomic writes; and make every
   lifecycle step resumable or reversible.
6. **Update downgrade/rollback.** Pin Steam manifests with an evidence trail,
   preserve the prior executable tree or validated artifact, and prove both
   forward recovery and rollback without overwriting saves.
7. **Player-affecting actions.** Capture before-state when available; use typed
   target selectors; require exact confirmation for destructive/offline actions;
   and verify the live or save-observed result.
8. **Remote control and chat.** Use scoped identities and allowlists, replay
   protection/rate limits, secret redaction, and an auditable mapping from remote
   user/role to local capability.

## Remaining work queue

Priority is execution order, not a decision to omit later rows.

| Priority | Milestone | Required outcomes | Confidence/risk |
| --- | --- | --- | --- |
| 1 | Operations console foundation | Implemented: session roles, navigation, versioned API, player-aware event feed, live filtered logs, retained metrics panels, browser diagnostics/support bundle, backup browser, guarded restore, categorized installed-build settings with current uncommon-setting metadata, presets, rendered/pending state and guarded raw editing. | High implementation confidence; metadata must remain synchronized with game-build drift |
| 2 | Lifecycle safety parity | Successful live protected Steam validation/update and restart is proven. Authentic older/current official images now prove executable round trips, same-volume forward persistence, incompatible-downgrade detection and verified prior-build recovery. Remaining operational evidence is live REST-stall/RSS injection and a configured external UDP probe. | High confidence in the update/rollback transaction; moderate confidence in recovery triggers until deliberate live fault injection is scheduled |
| 3 | Save intelligence and map | Implemented: source/SHA-pinned parser and species metadata, record-streamed profiles/inventory/Pals/Paldeck/guilds/bases/structures, exact base-worker joins, Pal/work search, optional bounded Game Data polling, explicit capability/schema drift, named POIs, optional worker/boss/Palbox layers and a coordinate picker feeding typed teleport and wild-Pal spawn plans. Current Palworld plus PalDefender under the supported Wine profile proved live command discovery and fail-closed application rejection; action success/observation is fixture-proven. | High confidence for read-only build `24181105` and the live backend boundary; moderate confidence for player-present mod actions until an expendable live player is available |
| 4 | Console and player administration | Implemented: private RCON transport, command catalog/history/saved/scheduled commands, enforced whitelist, discovered/hash-bound typed teleport/wild-spawn/item/Pal/status actions and protected offline rename/unused-UID migration/inventory/currency/player-progression/owned-Pal edits. The current save-write/process path and supported live PalDefender command catalog are proven; application rejection fails closed. | High confidence for official RCON, live whitelist enforcement, current schema/codec process path and supported PalDefender loading; moderate confidence for player-present action execution |
| 5 | Browser provisioning and multi-instance | Implemented: one-shot first-run browser planning/execution for fresh install or exact-fingerprint adoption with generated credentials and resumable handoff; supported Compose multi-instance manager with per-instance project/settings/ports/world ID/save state, isolated lifecycle/status, collision refusal and preserving deletion; guarded multi-world selection, imports and co-op host replacement. Optional future work is a browser surface over the proven manager contract. | High confidence from single-world browser bootstrap plus two-instance exact-image runtime and preservation evidence |
| 6 | Configuration depth and events | Implemented: current uncommon world-setting metadata, typed/raw Engine.ini, editable/previewable launch arguments, timed event transactions with automatic rollback, and corruption/environment/WorldOptions diagnosis and preservation-first recovery. | High confidence; game-version metadata needs maintenance automation |
| 7 | Mod and file lifecycle | Implemented: bounded explicit-root file manager with SHA-bound upload, preservation and quarantine; structured hash-bound current PalDefender configuration; live command discovery; UE4SS Lua state; and supported opt-in Wine runtime with pinned, verified UE4SS/PalDefender install/update/remove/rollback. Real current-server load, semantic config apply/reload/rollback, component removal and exact recovery are proven. | High confidence in file/config/lifecycle transactions and current loader compatibility; release pins must be reviewed after game/mod updates |
| 8 | Integrations and accessibility | Implemented: guild-scoped permissioned Discord slash-command control, Discord-to-game `/say`, supported Wine/UE4SS token-free game hook, hardened volatile host adapter, secured loopback game-event ingress, exact-confirmed reversible external Discord validator, read-only integration keys/OpenAPI, responsive private UI, localization framework and Chinese/Japanese translations. Remaining: execute the prepared validation transaction through an explicitly authorized live Discord guild. | High confidence in the Discord/API/UI boundaries and real game-hook load; external guild delivery requires operator credentials/authorization |
| 9 | Portability | Implemented and exact-image proven: official Compose deployment, graceful persistence adapter, single-replica Helm deployment with Ready/recycle/same-PVC world persistence, plus a source/index/manifest-pinned ARM64 Box64 Compose path with strict native-host/page-size/credential/device preflight and a proven AArch64 Box64 -> x86-64 SteamCMD chain. | High confidence for official `amd64` Compose and Kubernetes; moderate confidence for device-sensitive ARM64 emulation under its fail-closed support boundary |
| 10 | Advanced offline repair | Implemented: identity/graph diagnostics, rename, unused-UID migration, fail-closed joined-identity replacement, read-only sibling-world progression/inventory/party/Palbox transfer, exact inventory/currency/player-progression/owned-Pal edits, duplicate and complete-reference graph cleanup, plus save-clock inactive deletion with closed base/build/worker/item/spawner cascades for empty guilds. Current-build nested-schema correction, protected mutation and exact official-image reopen are proven from a production-derived read-only clone. | High confidence in transaction/current codec/process compatibility; moderate confidence for fixture-proven host/transfer/edit/duplicate/graph/base-cascade transformations where no expendable real identity graph was available |

## Definition of done

For each feature row:

- the operator outcome works from the supported Linux deployment;
- control-plane secrets and player identifiers do not cross into public data;
- mutations have explicit gates, audit records, bounded inputs and recovery;
- unit/fixture tests cover success, refusal and rollback paths;
- a lab validation record exists for service, backup, restore, update, mod or save
  behavior that cannot be proven by unit tests;
- README/operator docs describe setup, limits and recovery; and
- this matrix is updated from **Partial/Gap** to **Parity/Exceeds** with the
  validating commit and evidence.

Exact union parity is **not complete**. The strongest current advantages are
backup/recovery integrity, offsite continuity, public-data isolation, audit and
systemd-native operation. The sole remaining evidence gap is end-to-end delivery
through an explicitly authorized live Discord guild; production currently has
no Discord application configured. The current game hook, volatile adapter and
loopback relay boundary are otherwise process-proven.
