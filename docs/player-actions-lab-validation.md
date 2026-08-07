# Typed player-action fixture and browser validation

Validated on 2026-07-16 with mocked action responses, a disposable loopback
operations service, and live command discovery against current Palworld plus
PalDefender under Wine. Production was not mutated and a real player was not
contacted. The default native-Linux process cannot load PalDefender. The opt-in
Windows-under-Wine deployment is a supported, live-validated backend; see
[`mod-lifecycle-lab-validation.md`](mod-lifecycle-lab-validation.md).

## Typed transaction contract

- Capability status comes only from bounded live `getrconcmds` discovery.
  Missing discovery disables every action; an individually missing command
  disables only that operation.
- Teleport, coordinate wild-Pal spawn, item grant, Pal grant and status-point
  grant accept separate typed schemas. Platform identities, asset IDs,
  quantities, levels and finite world coordinates are bounded and cannot
  contain command separators or whitespace.
- A plan hashes the complete normalized target and parameters while returning
  only a 16-character target hash and the action-specific confirmation phrase.
- Execution rejects an altered target/parameter hash and requires one of
  `TELEPORT PLAYER`, `SPAWN PAL`, `GRANT ITEM`, `GRANT PAL`, or
  `GRANT STATUS POINTS`.
- The command uses the existing private RCON executor and retained administrator
  history. The separate audit record contains only action class, target hash and
  verification class.
- When `getpos` is advertised, teleport records before/after coordinates and
  fails if observed X/Y is more than 250 world units from the reviewed target.
  Spawn and grants honestly report `command-acknowledged`; PalDefender exposes
  no safe read-back for the spawned actor, inventory, Palbox, or unspent-point
  state.
- Current PalDefender 1.8.3 returned the semicolon-delimited
  `name:required_args` catalog and advertised all five actions plus `getpos`.
  The parser now accepts that live format. Attempts against the empty lab world
  returned `Invalid PalID`; application-level rejection text now fails closed
  instead of being mislabeled as acknowledgement.

## Browser/API validation

The administrator-only API returns capabilities and accepts plan/execute while
viewer access is refused. The Players panel transfers a clicked map point into
the typed coordinate fields, begins with both mutation buttons disabled, and
keeps them disabled when backend discovery is unavailable or errors.

Local headless Chromium rendered the real loopback service at 1440×900 and
390×844. The panel exposed seven typed controls, had no horizontal document
overflow at either size, and kept Plan/Execute disabled while PalDefender was
absent. The first mobile review found labels colliding because the new field
container inherited flex layout; a dedicated one-column mobile grid fixed it.
The final mobile render was inspected and the temporary service, browser
profile, screenshot and listeners were removed.

## Automated evidence

```text
python3 -m unittest -v tests.test_player_actions tests.test_ops_server tests.test_localization
node --check admin/static/app.js
python3 -m py_compile scripts/player-actions.py admin/ops-server.py
python3 -m unittest discover -s tests -q
```

The lab proves current loader/RCON discovery and the complete guarded action
path. Successful player-present teleport/grant and observable spawn are not
claimed; those success and teleport-observation paths are fixture-proven because
the disposable world had no expendable online player.
