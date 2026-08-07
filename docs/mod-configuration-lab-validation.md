# Structured mod-configuration fixture validation

Validated initially on 2026-07-16 against a disposable loopback operations service and a
temporary Palworld-shaped tree. Production was not contacted. The fixture used
an installed PalDefender `Config.json`, one disabled UE4SS Lua mod, fake
administrator credentials and no real game process.

## Transaction and refusal coverage

- The private status contract exposes 57 current typed scalar settings and MOTD while
  omitting fixture IPs and token-shaped unknown values.
- Boolean, integer and finite-number types, documented advisory ranges, MOTD
  line/count bounds, JSON size, ordinary-file and no-symlink contracts are
  enforced.
- Plan records changed managed keys, preserved-unknown count, current SHA-256
  and proposed SHA-256 without returning the complete configuration.
- Apply requires `APPLY MOD CONFIG` and the reviewed current hash. A concurrent
  file change is refused before writing.
- Unknown keys and identifier-bearing arrays survive the merge value-for-value.
- A mode-0600 snapshot is created under state; installed JSON and SHA-256 are
  re-read after the atomic write. Injected post-write validation failure restores
  the original bytes.
- Rollback is current-hash-bound and preserves the displaced current file as a
  new private snapshot.
- Lua discovery ignores `shared`, symlinked and invalid names. State changes
  require `SET LUA MOD STATE`, update a bounded `mods.txt`, remove a conflicting
  enable marker when disabling and never restart the game.
- Live command discovery parses bounded `getrconcmds` output, de-duplicates it,
  attaches known guidance and assigns unknown plugin commands critical risk.

## Browser/API validation

The real loopback operations service completed admin login, rendered all 57
fields, planned and applied `logRCON=false → true`, then enabled a fixture Lua
mod. Neither the fixture IP nor token-shaped unknown field appeared in the DOM.
The panel rendered at 1440×900 and 390×844. Both sizes had zero horizontal
overflow after constraining the expanded discovered-command selector; the mobile
panel width was 356 pixels in a 390-pixel viewport.

The in-app browser runtime was unavailable, so this render used local headless
Chromium through its debugging protocol. Desktop/mobile screenshots were
inspected during the lab and removed with the temporary server tree, browser
profile and listeners afterward.

## Automated evidence

```text
python3 -m unittest tests.test_mod_config tests.test_rcon
python3 -m unittest tests.test_ops_server.OpsServerTests.test_mod_configuration_routes_are_admin_only_and_exactly_confirmed
python3 -m unittest tests.test_localization
node --check admin/static/app.js
```

Current live PalDefender/UE4SS execution, generated-schema correction,
apply/reload/rollback and command discovery are recorded separately in
[`mod-lifecycle-lab-validation.md`](mod-lifecycle-lab-validation.md).
