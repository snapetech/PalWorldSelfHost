# Authentic build rollback and recovery validation

Validated on 2026-07-16 with two immutable official Pocketpair Linux images in
a disposable local Docker lab. Production `kspls0`, its install tree, services,
ports and saves were not contacted.

## Artifacts

| Game version | Official registry digest | Lab appmanifest build ID |
| --- | --- | --- |
| `v1.0.0.100427` | `sha256:3a36c93eba3ded457ad6c74acd19923642fafb74f4f675afe05b545b8726251c` | `24088465` |
| `v1.0.1.100619` | `sha256:0d293cafd503a91a6d11d71f7bf770ee0c3c5ecf37db988349b2c1758f4e9358` | `24181105` |

The older image was created on 2026-07-07 and the newer image on 2026-07-13.
Both registry manifests declare `linux/amd64`. Their server executable and pak
SHA-256 values differ, proving a real binary transition rather than a tag alias.
Steam's anonymous app metadata supplied the current build. The older build ID
was correlated from the [published Linux depot history](https://steamdb.info/depot/2394012/manifests/)
for the official image's release date. The disposable local appmanifests used by
`steam-build-manager.py` were therefore explicit mapping fixtures, not artifacts
downloaded from Steam.

Anonymous SteamCMD was also asked for historical Linux depot manifest
`7743228609268535996`. Steam exposed the metadata but correctly refused the
content with `missing license for depot (No subscription)`. No account
credential was used or requested; the immutable official image supplied the
older executable tree instead.

## Executable transaction

The current 7.54 GB package tree was snapshotted with 15 files, an appmanifest
hash and both critical hashes. The install was then replaced with the older
official image tree while a sentinel under `Pal/Saved` was retained. The older
tree was independently snapshotted. Plans and exact-confirmed restores then
completed current → older → current → older. Each transition re-read the mapped
build ID and expected server binary hash; the save sentinel remained byte-equal
through every `rsync --delete` transaction.

Automated tests additionally prove critical-file tampering refusal, exact
confirmation, local-build-only pinning, target-health failure recovery, prior
build REST verification, incomplete-recovery reporting and save preservation.

## Real server process drill

The older official image started through the shipped persistence adapter,
answered authenticated REST as `v1.0.0.100427`, and created a world on a private
Docker volume. It stopped through REST in two seconds. The newer official image
then started against the same volume as `v1.0.1.100619` with the identical world
GUID and stopped through REST in three seconds.

Starting the older image again against that now-newer save was a genuine failed
downgrade: Palworld reported `Save data version mismatch`, said the data was
created by a newer game, and exited 139. This is the upstream compatibility
boundary the recovery contract is designed for. Returning to the newer official
image restored authenticated REST with the same world GUID. The toolkit now
also verifies the recovered prior build's REST health; if that check fails, it
reports automatic recovery as incomplete and retains the protected save and
executable snapshots.

The result is parity under the audit's mitigation contract: known builds can be
pinned, authentic executable trees can be restored without overwriting saves,
and an incompatible downgrade fails back to a verified prior executable instead
of being declared successful. It does not claim that Pocketpair supports loading
every newer save in an older game version.

## Evidence commands

```text
python3 -m unittest -v tests.test_build_rollback tests.test_update_control tests.test_steam_build_manager
bash -n scripts/ops-build-rollback.sh
shellcheck -x -e SC1091 scripts/ops-build-rollback.sh
python3 -m unittest discover -s tests -q
```
