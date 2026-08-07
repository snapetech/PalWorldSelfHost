# Production Wine migration and direct save editing: incident and fixes

Recorded 2026-08-07 against production `kspls0`, after a routine request
("bigger Palbox, more coins") turned into a same-day production runtime
migration. Kept here because every failure below is a real, reproducible gap
in either this repository or in the wider Palworld save-tooling ecosystem,
not a one-off mistake — the next operator to touch the wine-windows runtime
or write a save-editing tool will hit the same things.

## What happened, in order

1. **`palworldctl settings apply` run as root took the server down.**
   `render_or_restore()` in `settings-manager.py` calls `start.sh
   --render-only` with whatever privileges invoked it. `update.sh` correctly
   drops to the `palworld` user via `runuser` before calling steamcmd, but
   this path has no such guard. Invoking it through `sudo -n bash -c '...'`
   (as root) left `Pal/Saved/Config/LinuxServer/Engine.ini` root-owned; the
   next normal service start, running as `User=palworld`, couldn't write it
   and crash-looped until systemd's `StartLimitBurst` tripped. Fix in the
   moment: `chown` the file back and `systemctl reset-failed`. **Standing
   fix: run every `palworldctl`/settings/mod-lifecycle invocation as `sudo -n
   -u palworld ...`, never as raw root.** Root is correct only for
   `systemctl` itself and for editing `/etc/palworld-server.env`, which is
   genuinely root-owned.

2. **The wine-windows runtime existed only in the working tree, not on
   `kspls0`.** `scripts/start.sh`, `palworld-common.sh`, `settings-manager.py`,
   `update.sh`, and `server-config-manager.py` all carried the
   `PALWORLD_RUNTIME=wine-windows` / `PALWORLD_CONFIG_PLATFORM` support
   locally (visible as uncommitted `git status` changes) but the versions
   deployed to `/usr/local/lib/palworld/` on production predated all of it.
   Flipping the runtime without redeploying these five files makes
   `start.sh` fall through to its old unconditional `exec ./PalServer.sh`
   branch and fail with `PalServer.sh: No such file or directory` the moment
   the Windows depot replaces the Linux one. Anyone repeating this migration
   must deploy those five files (or, better, actually land this branch —
   see below) before flipping the runtime, not after.

3. **UE4SS's pinned release hash had drifted.** `mod-lifecycle.py` pins
   `Okaetsu/RE-UE4SS@experimental-palworld` to a specific SHA-256, but that
   GitHub release tag is mutable — the maintainer re-uploaded
   `UE4SS-Palworld.zip` on 2026-07-19 (repackaging only, per the release
   notes; PalDefender's pin was unaffected). The lifecycle tool correctly
   refused the mismatched download rather than silently accepting it. Before
   ever updating a pin, verify the new hash against the asset's own
   GitHub-reported `digest` field
   (`gh api repos/<owner>/<repo>/releases/tags/<tag>`) and read the release
   notes for what actually changed — don't just accept whatever a fresh
   download produces.

4. **A cold Wine prefix hangs inside `rundll32 setupapi,InstallHinfSection`
   on first launch.** Letting `start.sh` trigger `wineboot` implicitly (by
   just running `wine ./PalServer.exe` against a prefix directory that
   doesn't exist yet) reliably hung for 20+ minutes on both the desktop lab
   host and `kspls0`, independent of CPU/memory pressure. Running `wineboot
   --init` **standalone**, once, against the target `WINEPREFIX` before ever
   invoking the game binary avoids the hang entirely — every subsequent
   `start.sh` run against that same prefix boots normally. Any install
   path that sets `PALWORLD_RUNTIME=wine-windows` for the first time should
   pre-initialize the prefix this way rather than relying on the implicit
   first-run path.

5. **The Windows binary did not recognize the existing native-Linux world
   save and silently created a new, empty one.** After the runtime actually
   came up clean, REST reported a different `worldguid` than before, and the
   connected player showed as a fresh level-1 character. The original save
   (`Pal/Saved/SaveGames/0/<guid>/`) was untouched on disk — the Windows
   depot just computed a different target world GUID and made its own
   sibling directory. This is exactly the risk `deploy/wine-modded/README.md`
   already warns about ("Do not change an existing production runtime in
   place... prove client compatibility before scheduling a migration"), and
   it is why that warning exists: **this specific compatibility question —
   does a wine-windows depot reuse an existing native-Linux `SaveGames/0/`
   directory, or fork a new world — is still open and must be answered in a
   disposable lab, on a copy of the save, before any real migration.**
   Production was rolled back to native-linux (re-pull the Linux depot with
   plain `steamcmd ... +app_update 2394010 validate`, revert
   `PALWORLD_RUNTIME`) rather than chase this live.

None of the above blocks the wine-windows runtime from ever being used
safely — PalDefender and UE4SS installation, config, and Lua mod
enable/disable all worked exactly as designed once the runtime was actually
reachable (see `docs/mod-lifecycle-lab-validation.md`). It blocks doing it
*in place, on production, under time pressure*, which is the one thing the
existing docs already said not to do.

## Palworld's save format changed to Oodle mid-2026

Separately from the runtime migration: granting a player an item/currency
adjustment has no vanilla or native-Linux-RCON path (`give`-style commands
are a PalDefender addition, i.e. also gated behind the wine-windows
migration above). Editing the world save file directly is runtime-agnostic
and doesn't require any of the above — but as of the mid-2026 update,
Palworld saves carry a `PlM` magic (Oodle/Kraken compression) instead of the
`PlZ` (zlib) that every existing Python save-editing tool, including
`palworld-save-tools` on PyPI (last published 0.24.0), still expects.
Decompression fails immediately with `not a compressed Palworld save, found
b'PlM' instead of b'PlZ'` — this is a known, still-open gap in that project
(see issues #214, #219, #221, #222 and unmerged PRs #215/#216 upstream).

The practical fix, implemented in `third_party/ooz/` and
`scripts/save-currency-edit.py`:

- **Decompression**: [powzix/ooz](https://github.com/powzix/ooz) is a
  long-standing open reimplementation of Oodle's Kraken decompressor.
  `third_party/ooz/fetch-build.sh` downloads its three source files from a
  pinned commit, verifies each against a reviewed SHA-256, and builds a
  small native Linux binary (`palworld_decompress`) around them — no Wine
  required; the handful of Windows-only symbols the upstream source expects
  (`_BitScanReverse`, `_byteswap_ulong`, SSE2 intrinsics, etc.) are shimmed
  in `stdafx.h`, which is original to this project. The upstream repository
  declares no license, so its source is fetched and verified at build time
  rather than vendored into this repository's history.
- **Compression**: no Oodle *compressor* is needed. The game still accepts
  plain zlib (`PlZ`, save type `0x31`) on load regardless of which format it
  last wrote — this was confirmed by writing a real player's currency edit
  back as `PlZ` and having the live production server load it cleanly.
  `palworld-save-tools`'s existing zlib path already does this correctly
  unmodified.
- **Parsing depth**: `palworld-save-tools`'s custom raw-data decoders for
  characters, map objects, base camp modules, work assignments, and
  connectors all raise `Warning: EOF not reached` against current saves —
  the underlying structures gained fields the library predates. Patching
  all of them individually turned out to be the wrong approach: several
  (`connector.py` in particular) parse with a greedy loop whose item
  boundary becomes ambiguous once the per-item shape has changed, so
  "capture the leftover bytes" isn't actually safe there. The fix that
  worked: **don't decode what you don't need to edit.** `GvasFile.read()`
  only invokes a custom decoder for a property path that's present in the
  `custom_properties` dict you pass it; anything else is read back as
  opaque, unparsed bytes and therefore reproduced byte-for-byte on write
  with zero risk of misinterpretation. `save-currency-edit.py` registers
  only `.worldSaveData.ItemContainerSaveData.Value.RawData` (`item_container.py`,
  which parses cleanly against the current format unmodified) and lets
  everything else — every base, every Pal, every building — pass through
  untouched.
- **Verification before any write**: decode with zero changes, re-encode,
  and require the output to be byte-for-byte identical to the input before
  trusting the pipeline at all (this passed against the full 27.6 MB
  production `Level.sav` with the filtered property set above). Then, for
  the real edit, require the post-edit re-encode to differ from the
  original by only a handful of contiguous bytes, and self-verify the
  freshly-recompressed `.sav` decompresses back to exactly the intended
  bytes — all before the real file on disk is ever touched. See `apply()`
  in `scripts/save-currency-edit.py`.

## Using the new tool

```
scripts/save-currency-edit.py plan <player-save-id> <item-id> <delta>
scripts/save-currency-edit.py apply <player-save-id> <item-id> <delta> \
    --expected-plan-hash <hash> --confirm "APPLY SAVE ITEM EDIT"
```

`<player-save-id>` is the 32-character hex filename stem under
`Pal/Saved/SaveGames/0/<world>/Players/` (visible via `palworldctl players`
→ `playerId` while the server is running). The server must be stopped for
`apply`; `plan` is read-only and safe to run anytime. Requires
`third_party/ooz/fetch-build.sh` to have been run once and the
`palworld-save-tools` PyPI package installed.
