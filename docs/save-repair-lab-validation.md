# Offline save-repair fixture validation

Validation was performed on 2026-07-16 in disposable local directories. The
production Palworld host was contacted only to copy its active save through a
read-only SSH/rsync workflow. It was not stopped, reconfigured or modified, and
matching before/after remote hashes proved that the copy operation left every
source byte unchanged.

## Peer and codec audit

The qualifying magicbear toolkit at pinned commit
`d5cccfe0c6ef200e5b433c7cc37e9cd6986d1199` was re-audited for its documented
rename, player migration/copy, inactive-player deletion, duplicate repair and
unreferenced/broken-container cleanup outcomes. The palserver GUI host-fix
workflow at pinned commit `b047192fe0cee5b2e828dc12b2af89d0f1122004`
was inspected only for operator behavior because its license is noncommercial.

The separately executed GPL codec remains pinned to source commit
`2c8c65c4a60b04e63eeb7f0c1857a5ba903a24d9` and binary SHA-256
`f9a7e17782c233ba1be286ce3239447046024c76e2b5f5b375cb25f00743948e`.
Its advertised player/base backup commands are not usable: the published binary
raises `ModuleNotFoundError` because `palworld_aio` was omitted, and the pinned
source CLI also passes arguments that its manager signatures do not accept.
PalWorldSelfHost therefore uses only the independently verified core `convert`
surface and implements its transformations and transaction itself.

## Real codec round trip

A real upstream Palhelm `Level.sav` test fixture with SHA-256
`4706ee40f81799f9f5b5b3ab60a7239dee5c95654a085dbaae6b21b09aa38fbb`
was decoded, encoded with zlib, and decoded again. The encoded SAV legitimately
had a different byte hash, while the two minified decoded JSON artifacts were
byte-identical at 435,503 bytes. A second pass through
`scripts/save-repair.py` required full decoded object equality and retained the
same save/engine/custom-version/section schema signature.

## Transaction and browser validation

Thirty-four focused tests cover:

- exact rename propagation into the player and guild-member records;
- duplicate-player ambiguity, invalid names and existing target refusal;
- whole-value GUID replacement in Level and player saves without replacing GUID
  substrings embedded in unrelated text;
- existing-identity replacement that removes the joined placeholder, reuses its
  instance ID, removes an empty placeholder guild and refuses non-disposable
  owned references;
- duplicate cleanup that retains the sole player-save/guild-backed instance,
  removes its stale character and guild handles, and refuses conflicting
  canonical evidence or remaining references to the stale instance;
- complete-reference graph cleanup that repairs dangling character and dynamic
  item slots, removes unreferenced character/item containers and cascading
  dynamic items, retains implicitly group-owned item containers, and refuses
  duplicate container identities;
- save-clock-based inactive cleanup that removes profiles, owned Pals, empty
  guilds and cascading containers/items while refusing retained guild members,
  invalid thresholds and residual world ownership;
- base-owning empty-guild cleanup that traverses base camps, workers, work
  records, Palbox/connected structures, item containers, dynamic items and
  spawners, with reverse-reference refusal for shared retained data;
- cross-world transfer of progression, inventory, party and Palbox graphs into
  a disposable joined target while retaining its UID, instance and guild;
- source-world all-byte plan binding and read-only staged application, plus
  refusal of external target ownership, missing dynamic items and arbitrary
  source paths;
- exact inventory/currency slot mutation through a player-save-bound container,
  empty-slot consistency, dynamic-metadata replacement refusal and observed
  staged encoding without rewriting the player save;
- bounded player level/experience edits and owned-Pal nickname, level, rank,
  talent and passive edits with identity/ownership verification;
- duplicate UID/instance, missing/orphan player-file and broken guild-member
  diagnostics;
- build/parser/schema/all-file plan binding and fingerprint drift;
- case-insensitive player-save discovery, case-collision refusal and a
  fail-closed writable-build allowlist;
- inactive-world success with prior-world preservation; and
- active-world REST-startup failure with the original world restored and the
  protected backup occurring before service stop.

## Current-build save and process drill

The active world from production build `24181105` was copied into a private
disposable directory. Complete remote-before, remote-after and local SHA-256
manifests matched on the first consistency attempt. The snapshot contained save
version 3, Unreal Engine 5.1.1 data and the same required-section signature
accepted by the writable allowlist.

The first rename attempt exposed a real schema difference that the fixtures did
not: current guild membership stores the display name under
`player_info.player_name`, while older saves can use the legacy top-level
`player_name`. The implementation was corrected to update only recognized
existing fields and to refuse a matching membership with neither shape. A
focused regression test covers that current nested form.

On a second private clone, a protected rename plan and staged apply completed
codec encode/decode structural-equality verification with the schema signature
unchanged. The mutated clone was mounted into Pocketpair's immutable official
`v1.0.1.100619` image. Authenticated REST became healthy with the exact
pre-existing world GUID; the server then exited 0 without OOM, created no
replacement world directory, and the stopped save decoded with the staged name
still present. No production process, save, service, port or network rule was
changed. Both production-derived disposable copies and all validation
containers were removed after the evidence was captured.

An authenticated local Chromium pass exercised the real admin page at 1440px
and 390px. Rename/migration/transfer/graph/inactive switching exposed only the
relevant fields; transfer emitted only its sibling world ID, source UID and
joined target UID, while inactive cleanup emitted only its action and bounded day threshold;
inventory/currency, player-progression and owned-Pal states emitted only their
typed action fields, optional blank progression values were omitted, comma-separated
passives became a bounded array, and all eight visible Pal fields stayed at 300px
inside the 390px viewport;
apply remained disabled without a reviewed plan; a deliberately rejected
backend plan stayed fail-closed and reported that the save was unchanged; all
mobile fields stayed within a 390px viewport; and no horizontal overflow
occurred.

## Supported boundary

The shipped transaction supports player rename, same-world migration to an
unused UID, fail-closed existing-identity/co-op-host replacement,
unambiguous duplicate-player cleanup, read-only sibling-world player transfer
into a disposable joined target, exact inventory/currency and player-progression
edits, bounded owned-Pal edits, and complete-reference broken/unreferenced
container cleanup on live-validated game build `24181105`; other builds fail
closed until their writable schema is validated. Inactive deletion supports
candidates whose guild becomes empty, including base/build/worker cascades when
the complete ownership graph is closed and has no retained reverse reference;
retained members, malformed entities and shared or unknown ownership fail closed. Transfer
copies player progression, inventory, party and Palbox data, preserves the
source, and retains the target-world identity and guild; it refuses ambiguous or
colliding graphs. Current-build production-derived schema, codec write and
official-server reopen compatibility are process-proven; identity replacement,
transfer, duplicate repair and base cascades retain direct success and
fail-closed fixture coverage because the production snapshot did not contain
disposable identity graphs suitable for those destructive exercises.

Confidence is **high** for the transaction, current-build schema/codec path,
rename process compatibility and rollback/refusal boundaries, and **moderate**
for identity replacement, transfer and base-cascade transformations whose
current-build evidence remains fixture-based.
