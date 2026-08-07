# Private Operations Console

The operations console is a private, role-aware control plane for one managed
Palworld world. It exposes versioned `/api/v1` routes and serves its browser UI
from the same origin. Keep it on loopback, a trusted LAN, or a VPN; never expose
it directly to the public internet.

## Roles and sessions

Configure independent random tokens in `/etc/palworld-server.env`:

```dotenv
PALWORLD_OPS_TOKEN=administrator-token
PALWORLD_OPS_MODERATOR_TOKEN=optional-moderator-token
PALWORLD_OPS_VIEWER_TOKEN=optional-viewer-token
PALWORLD_OPS_INTEGRATION_TOKEN=independent-read-only-bearer-token
PALWORLD_OPS_SESSION_TTL_SECONDS=28800
PALWORLD_OPS_SECURE_COOKIE=false
```

| Role | Access |
| --- | --- |
| Viewer | Status, redacted roster/save profiles, guild/base/map views, backup metadata, retained metrics, redacted events and bounded logs |
| Moderator | Viewer access plus announcements and explicitly enabled kick/ban/unban actions |
| Administrator | All surfaces, lifecycle/backup actions, backup downloads/verification/quarantine, settings, private RCON/saved commands, enforced whitelist and scheduled work |

The browser exchanges a role token for an in-memory session. The cookie is
`HttpOnly` and `SameSite=Strict`; state-changing calls also require the session's
CSRF token. Login attempts are throttled per client address. Sessions disappear
when `palworld-ops.service` restarts and expire after the configured TTL.

The unauthenticated shell and every private surface can be operated in English,
Simplified Chinese, or Japanese. The selector persists only the locale in local
browser storage; `?lang=en`, `?lang=zh-Hans`, and `?lang=ja` provide explicit
entry links. Translation covers all static operator text, placeholders and
accessibility labels plus core dynamic health/action states. API schemas, JSON
keys and exact destructive confirmation phrases intentionally stay in English
so automation and reviewed safety contracts do not vary by locale.

For a device that should not receive a persistent role token, create a
short-lived one-use code on the host, then enter it in the same password field
and choose **Use one-time pair code**:

```bash
sudo -u palworld /usr/local/lib/palworld/pairing.py create \
  --role viewer --ttl 300 --confirm 'CREATE PAIRING CODE'
```

Codes contain 12 unambiguous random characters, are stored only as salted
hashes, expire after 60-600 seconds, are consumed atomically once, inherit the
normal session TTL/CSRF/cookie controls, and never become an API credential.

Set `PALWORLD_OPS_SECURE_COOKIE=true` when the console is served through HTTPS.
Leave it false for direct loopback HTTP, because browsers do not send a Secure
cookie over plain HTTP.

The original `X-Palworld-Ops-Token` header remains a non-browser administrator
API credential. Optional viewer and moderator tokens work in the same header
and receive their configured role. This preserves scripts while the browser no
longer stores the administrator token in local storage.

`PALWORLD_OPS_INTEGRATION_TOKEN` is a separate bearer credential. It cannot
create a browser session, call ordinary viewer routes, or mutate state. It is
accepted only by the three `/api/v1/integration/*` GET routes below. The
machine-readable contract is available without credentials at
`/api/v1/openapi.json`; keep the service itself on private ingress.

## Browser surfaces

- current service, world, player, update, health and maintenance state;
- role-aware start/resume, exact-confirmed graceful stop, save, backup, restart,
  announcement and moderation controls;
- backup inventory with manifests/checksum state, administrator download,
  on-demand verification, guarded plan/restore and reversible quarantine;
- 90-day retained FPS, frame-time, player, uptime, RSS and restart data through
  a bounded API, with selectable 6-hour through 90-day browser charts;
- allowlisted systemd-unit logs with bounded time/line windows, literal filters,
  five-second live polling, and redaction before leaving the server;
- browser channel diagnostics for world/REST/health/backup/storage/update and
  maintenance state, plus an administrator-only redacted support bundle that is
  generated on demand, streamed, and removed immediately;
- loop-protected recovery after consecutive REST/world-uptime stalls and at an
  optional RSS threshold, with player-aware memory-restart deferral;
- opt-in fail-open auto-pause after a continuously verified empty-roster window,
  with audited paused state and on-demand administrator resume;
- hourly/on-demand IPv4/IPv6 listener inspection that verifies the fail-closed
  nftables boundary around Palworld's wildcard REST/RCON listeners and flags an
  unsafe operations bind, plus an optional HTTPS external UDP reachability probe;
- on-demand world/Engine INI, environment and WorldOptions conflict diagnosis,
  with preservation-first stopped-world recovery;
- Steam update check and reviewed plan/execution bound to the exact target build;
- local executable snapshots, build pin/unpin, rollback planning and guarded
  rollback that excludes `Pal/Saved` and restores the prior tree on failure;
- redacted audit/event feed including five-minute derived player joins/leaves,
  lifecycle, maintenance, health, settings, backup, restore and moderation;
- searchable online/offline player history with first/last seen, sessions and
  retained duration, and identity visible only to moderator/administrator roles;
- source/SHA-pinned read-only save scans with explicit build/schema state,
  offline progression, inventory, party/Palbox Pals, Paldeck, guilds, bases and
  offline-player/base/structure coordinate layers;
- admin-only offline save diagnostics plus hash-reviewed rename, unused-UID
  migration, disposable joined-identity replacement and unambiguous duplicate
  cleanup with zero-roster, protected-backup, stopped staging, structural
  round-trip verification, atomic swap and automatic world rollback;
- categorized/searchable typed settings with descriptions, advisory ranges,
  staged presets, rendered/restart-pending state and transactional rollback;
- guarded raw INI plan/apply/rollback that preserves comments and unknown fields,
  masks/restores managed secrets and automatically restores on render failure;
- typed Engine.ini network, frame-rate and garbage-collection tuning with
  documented ranges and staged presets, plus guarded raw Engine.ini editing;
- typed launch-option editing and preview for server listing, log format, worker
  threads and current/legacy performance flags; and
- private RCON status and official-command catalog, validated free-form
  execution, bounded actor/result/output history and saved commands;
- staged identity allow-list editing with plan/replace, exact enable/disable,
  a 60-second grace period and 15-second live enforcement;
- scheduled announcements, maintenance restarts and exact-confirmed one-shot or
  recurring direct/saved RCON commands; and
- exact-confirmed Double XP, resource and capture events that restart into the
  preset, restore only affected settings automatically, retry failed restoration,
  and restore early when cancelled.

Quarantine moves an archive and its checksum/manifest sidecars into
`$PALWORLD_BACKUP_LOCAL_ROOT/.quarantine/`. It does not delete them. Recovery is
a host-side move back into the backup root followed by `verify-backup.sh`.

## Versioned API

Read routes require viewer or better unless marked public:

| Method | Route | Minimum role | Outcome |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/login` | Token | Create role session and CSRF token |
| `POST` | `/api/v1/auth/pair` | One-use code | Atomically consume a short-lived role-scoped code into a normal session |
| `GET` | `/api/v1/auth/session` | Viewer | Inspect current session |
| `POST` | `/api/v1/auth/logout` | Viewer | Revoke current session |
| `GET` | `/api/v1/status` | Viewer | Current operational aggregate |
| `GET` | `/api/v1/events?limit=100` | Viewer | Redacted unified audit/player event feed, maximum 500 |
| `GET` | `/api/v1/logs?unit=palworld.service&lines=200&since_minutes=60&filter=error` | Viewer | Redacted, literal-filtered allowlisted journal; maximum 1,000 lines/7 days |
| `GET` | `/api/v1/metrics/history?hours=24&limit=720` | Viewer | Downsampled retained metrics, maximum 90 days/2,000 points |
| `GET` | `/api/v1/players/history?search=NAME&limit=200` | Viewer | Searchable retained player/session roster; identity requires moderator |
| `GET` | `/api/v1/save/intelligence` | Viewer | Last offline profile/guild/base/map snapshot; platform identity requires moderator |
| `GET` | `/api/v1/save/repair` | Administrator | Supported offline mutation contract and last successful result |
| `GET` | `/api/v1/game-data` | Viewer | Bounded optional actor projection or explicit capability/freshness state |
| `GET` | `/api/v1/map/locations` | Viewer | Bounded named map locations used by the private coordinate field |
| `GET` | `/api/v1/files?root=config&path=.&read=false` | Administrator | Browse explicit config/mod/log roots or read an allowlisted UTF-8 file |
| `PUT` | `/api/v1/files/upload?root=config&path=FILE` | Administrator | Stream a SHA-bound, exact-confirmed upload up to 64 MiB with preservation |
| `POST` | `/api/v1/files/quarantine` | Administrator | Move one regular config/mod file to reversible host quarantine |
| `POST` | `/api/v1/save/intelligence/scan` | Administrator | Flush, copy and source/SHA-pinned read-only scan of the active save |
| `POST` | `/api/v1/save/repair/diagnose` | Administrator | Decode the one managed Level plus every player save and report identity and broken/unreferenced graph evidence without writing |
| `POST` | `/api/v1/save/repair/plan` | Administrator | Require a zero roster, flush and hash-bind a rename, unused-UID migration, joined-identity replacement, read-only sibling-world player transfer, exact inventory/currency slot edit, player progression edit, owned-Pal edit, duplicate/graph cleanup or save-clock inactive-profile deletion plan |
| `POST` | `/api/v1/save/repair/apply` | Administrator | Exact-confirm the reviewed stopped-world transaction and automatic startup rollback |
| `GET` | `/api/v1/diagnostics/bundle` | Administrator | Stream a fresh redacted support archive, then remove it |
| `GET` | `/api/v1/diagnostics/exposure` | Viewer | Last redacted listener/public-probe result |
| `POST` | `/api/v1/diagnostics/exposure/check` | Administrator | Run listener and configured external probe now |
| `GET` | `/api/v1/diagnostics/config` | Administrator | Diagnose INI structure, environment keys/ports and WorldOptions conflicts |
| `POST` | `/api/v1/diagnostics/config/recover` | Administrator | Stop the world and exact-confirm preservation-first INI/conflict recovery |
| `GET` | `/api/v1/updates/status` | Viewer | Last persisted Steam update state |
| `POST` | `/api/v1/updates/check` | Administrator | Refresh installed/remote Steam build state |
| `POST` | `/api/v1/updates/plan` | Administrator | Review players, target build and maintenance steps |
| `POST` | `/api/v1/updates/install` | Administrator | Exact-confirmed protected update bound to target build |
| `GET` | `/api/v1/builds` | Viewer | Installed build, pin and verified local snapshot inventory |
| `POST` | `/api/v1/builds/plan` | Administrator | Verify snapshot and preview save-preserving rollback |
| `POST` | `/api/v1/builds/pin` | Administrator | Exact-confirmed pin of installed/snapshotted build |
| `POST` | `/api/v1/builds/unpin` | Administrator | Exact-confirmed return to automatic updates |
| `POST` | `/api/v1/builds/rollback` | Administrator | Protected, exact-confirmed executable rollback |
| `GET` | `/api/v1/backups` | Viewer | Backup inventory and manifests |
| `GET` | `/api/v1/backups/NAME` | Viewer | One constrained backup record |
| `GET` | `/api/v1/backups/NAME/download` | Administrator | Download one constrained archive |
| `POST` | `/api/v1/backups/verify` | Administrator | Run packaged checksum/archive verification |
| `POST` | `/api/v1/backups/restore/plan` | Administrator | Verify archive and return the restore plan |
| `POST` | `/api/v1/backups/restore` | Administrator | Execute exact-confirmed guarded restore |
| `POST` | `/api/v1/backups/quarantine` | Administrator | Move archive and sidecars to quarantine with exact confirmation |
| `GET` | `/api/v1/settings/schema` | Viewer | Current generated settings schema |
| `GET` | `/api/v1/settings/raw` | Administrator | Secret-masked raw INI and protected-field contract |
| `POST` | `/api/v1/settings/plan` | Administrator | Validate and preview changed values |
| `POST` | `/api/v1/settings/apply` | Administrator | Apply exact-confirmed settings plan |
| `POST` | `/api/v1/settings/rollback` | Administrator | Exact-confirmed rollback to the newest typed snapshot |
| `POST` | `/api/v1/settings/raw/plan` | Administrator | Validate and return a secret-redacted unified diff |
| `POST` | `/api/v1/settings/raw/apply` | Administrator | Exact-confirmed transactional raw render |
| `POST` | `/api/v1/settings/raw/rollback` | Administrator | Exact-confirmed raw snapshot rollback |
| `GET` | `/api/v1/server-config/schema` | Administrator | Engine/launch metadata, current values, presets, launch preview and pending state |
| `GET` | `/api/v1/server-config/raw` | Administrator | Current raw Engine.ini source and size contract |
| `POST` | `/api/v1/server-config/plan` | Administrator | Validate typed Engine/launch changes and preview rendered Engine.ini |
| `POST` | `/api/v1/server-config/apply` | Administrator | Exact-confirmed transactional Engine/launch apply |
| `POST` | `/api/v1/server-config/rollback` | Administrator | Exact-confirmed typed Engine/launch snapshot rollback |
| `POST` | `/api/v1/server-config/raw/plan` | Administrator | Validate and diff raw Engine.ini |
| `POST` | `/api/v1/server-config/raw/apply` | Administrator | Exact-confirmed transactional raw Engine.ini render |
| `POST` | `/api/v1/server-config/raw/rollback` | Administrator | Exact-confirmed raw Engine.ini snapshot rollback |
| `POST` | `/api/v1/action/{start,save,backup,restart,stop}` | Administrator | Guarded lifecycle action; stop requires exact `STOP SERVER` confirmation |
| `POST` | `/api/v1/{announce,kick,ban,unban}` | Moderator | Announcement or gated moderation |
| `GET` | `/api/v1/rcon` | Administrator | Private-boundary/connection state, official catalog, saved commands and bounded history |
| `POST` | `/api/v1/rcon/execute` | Administrator | Execute a validated command with exact `EXECUTE RCON` confirmation |
| `POST` | `/api/v1/rcon/saved` | Administrator | Create a validated saved command with exact confirmation |
| `POST` | `/api/v1/rcon/saved/ID/execute` | Administrator | Exact-confirmed saved-command execution |
| `POST` | `/api/v1/rcon/saved/ID/delete` | Administrator | Exact-confirmed deletion; refused while enabled jobs reference it |
| `POST` | `/api/v1/rcon/toggle` | Administrator | Zero-player protected enable/disable with backup, restart, health proof and rollback |
| `GET` | `/api/v1/whitelist` | Administrator | Current allow-list, enforcement state and last run |
| `POST` | `/api/v1/whitelist/plan` | Administrator | Validate and diff a proposed identity list |
| `POST` | `/api/v1/whitelist/replace` | Administrator | Exact-confirmed transactional list replacement |
| `POST` | `/api/v1/whitelist/{enable,disable}` | Administrator | Exact-confirmed enforcement transition |
| `POST` | `/api/v1/jobs` | Administrator | Create scheduled work, RCON work or an exact-confirmed bounded timed event |
| `POST` | `/api/v1/jobs/ID/cancel` | Administrator | Disable pending work or request immediate active-event restoration |
| `GET` | `/api/public-status` | Public | Legacy sanitized public aggregate |
| `GET` | `/api/v1/openapi.json` | Public | OpenAPI 3.1 contract for the scoped integration API |
| `GET` | `/api/v1/integration/status` | Integration bearer | Sanitized server/player aggregate; no operator state |
| `GET` | `/api/v1/integration/events?limit=50` | Integration bearer | Bounded redacted event feed, maximum 100 |
| `GET` | `/api/v1/integration/game-data` | Integration bearer | Aggregate capability, freshness, FPS and actor counts; no actor rows |

Legacy `/api/...` private route names remain aliases, but now require a valid
session or role token. New integrations should use `/api/v1`.

## Security boundaries

- Host and Origin must match on browser POST requests.
- Responses set CSP, frame denial, no-referrer and MIME-sniffing protections.
- Request bodies are capped at 64 KiB.
- Backup names must match the managed archive pattern and remain below the
  configured backup root after resolution.
- Journal units, line/time windows and literal filters are allowlisted or bounded.
- Viewer rosters redact network/platform identifiers and credential-named
  settings.
- Audit and log output redact IP addresses, platform/user identifiers and
  credential-like values for every role.
- Player presence retains private platform identity, display name, first/last
  seen, session count/duration and online state in SQLite. Network addresses are
  never copied into this history; browser events redact the identity key.
- Save intelligence executes a GPL codec as a separately downloaded process;
  the binary hash and corresponding upstream commit are immutable pins. It
  reads only a private temporary copy, streams bounded records, deletes decoded
  intermediates, persists parser/build/save provenance, and reports missing
  required sections or field errors as degraded schema rather than guessing.
  Viewer responses redact platform, character, owner and admin identifiers.
- Health recovery requires consecutive stall samples, holds the shared mutation
  lock, and enforces both a cooldown and a maximum restart count per time window.
  Memory recovery defaults off until `PALWORLD_MEMORY_RESTART_GIB` is set and
  defers when players are online or the roster cannot be proven empty.
- Exposure checks never infer internet safety from a port number alone. Local
  bindings are inspected directly. A root-owned service installs verified
  loopback accepts plus non-loopback drops for TCP 8212/25575 before
  `palworld.service` may start; wildcard REST/RCON binds count as private only
  while those exact rules verify. Public UDP reachability is only marked proven
  when a configured HTTPS probe returns the documented boolean contract.
- RCON is disabled by default. Enable/disable refuses online players, holds the
  shared mutation lock, creates a verified protected backup, changes only
  `PALWORLD_RCON_ENABLED`, restarts and verifies REST plus RCON. Any failure
  restores the prior environment and restarts it. Commands are loopback-only,
  one line/512 UTF-8 bytes, output-capped, exactly confirmed and audited by verb;
  `AdminPassword` is explicitly forbidden so credentials cannot enter history.
- Whitelist enforcement is disabled by default and cannot enable with an empty
  list. Replacement validates at most 500 unique platform identities. Enable
  grants a 60-second grace period; the 15-second timer then compares only a
  successfully fetched live roster and kicks non-members. A missing/malformed
  roster causes no player action; audit records rejected identities by truncated
  SHA-256 rather than raw platform ID.
- Browser updates re-query Steam inside the privileged wrapper and refuse when
  the reviewed target changed or a build is pinned. A verified protected save
  backup must succeed before mutation. The stopped executable tree is copied to
  a local build snapshot with appmanifest and critical-file hashes; `Pal/Saved`
  is excluded. Steam/render/post-start health failure restores and pins the prior
  tree. Explicit downgrade follows the same plan/backup/health/automatic-recovery
  contract and uses checksum-mode restoration.
- Backup downloads are administrator-only and every download is audited.
- Restore archives reject paths outside managed roots, links, special files,
  excessive member counts and excessive expanded size before extraction.
- Browser restore uses the existing stopped-world/player refusal, protected
  pre-backup, staging, GUID verification and automatic rollback transaction.
- Raw INI bodies are capped at 48 KiB; protected network/identity/credential
  fields must remain present, are restored from the trusted server copy, and do
  not appear in browser diffs. Unknown fields and comments remain intact.
- Typed and raw settings write rollback snapshots before atomic rendering. A
  render failure restores the prior source. Successful changes remain marked
  restart-pending until `palworld.service` reaches `ExecStartPost`.
- Engine and launch configuration follows the same plan/exact-confirm/apply/
  rollback contract. Raw Engine.ini is capped at 60 KiB and syntax-validated;
  comments, plugin sections and unknown options are retained while typed values
  remain authoritative. `start.sh` re-renders Engine.ini and reads the saved
  launch arguments before every process start, countering game-side rewrites.
- Timed settings events accept only built-in presets and durations from five
  minutes through seven days. At event start the scheduler snapshots whether
  each affected override existed and its value. Restoration changes only those
  keys, preserving unrelated operator edits; failed restoration retries every
  minute and active-event cancellation requests the same restore path.
- Configuration recovery never deletes a suspect file. It stops the world,
  renames the active corrupt INI or conflicting WorldOptions save, regenerates
  from installed/authoritative state, validates the result, and puts the suspect
  file back if generation fails. Environment health reports malformed/duplicate
  keys and port conflicts without returning secrets. A successful install saves
  a root-readable `env-known-good`; root CLI recovery can preserve and replace a
  damaged environment file with `config-recovery.py repair environment`.
- Moderation retains the separate `PALWORLD_MODERATION_ENABLED` gate and exact
  ban confirmation.

## Validation

Run:

```bash
python3 -m py_compile admin/ops-server.py
node --check admin/static/app.js
python3 -m unittest discover -s tests -v
```

`tests/test_ops_server.py` starts the real HTTP handler on an ephemeral port and
proves role sessions, CSRF refusal, role-scoped backup/support downloads,
traversal refusal, nested/log redaction, literal log filtering and retained-
history bounds. `tests/test_settings_manager.py` proves metadata, secret masking,
raw preservation/refusal, transactional apply, pending state and rollback.
`tests/test_server_config_manager.py` proves Engine ranges/presets, launch
preview, preservation of unmanaged INI content, input refusal, pre-start wiring
and typed/raw rollback. `tests/test_scheduler.py` proves timed-event activation,
affected-key capture, automatic restoration, cancellation restoration and
one-shot/recurring RCON dispatch. `tests/test_rcon.py` proves Source-RCON auth,
Palworld response IDs, loopback/input boundaries and console persistence;
`tests/test_whitelist.py` proves transaction gates, grace, selective rejection
and no-action roster failure. `tests/test_exposure.py` proves firewall-protected
wildcard REST/RCON handling.
`tests/test_config_recovery.py` proves corruption/conflict detection, preserved
world/Engine regeneration, environment port diagnosis and WorldOptions disable.
`tests/test_save_intelligence.py` proves record-streamed profile, inventory,
Pal, guild, base and map extraction, explicit schema drift and parser-hash
refusal; the operations-server suite proves viewer redaction and admin-only scan.
`tests/test_backup.py` proves staged publication and rejects
traversal/link archives through the real verification workflow.
