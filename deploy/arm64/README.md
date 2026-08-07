# ARM64 Box64 deployment

This is the supported ARM64 server-only path for 64-bit ARM Linux hosts with
4 KiB pages. It uses the qualifying peer release `v2.6.0` at a pinned
multi-platform index digest; Compose selects its reviewed ARM64 manifest and
that image runs Palworld's x86-64 Linux server through Box64. It does not alter
or replace the native `kspls0` deployment.

The emulator is slower and may require device-specific tuning. Hosts with 16
KiB or 64 KiB pages fail preflight. `generic`, Apple `m1`, Raspberry Pi 5
`rpi5`, and Oracle/Ampere-style `adlink` Box64 builds are accepted. The shipped
defaults choose the peer's conservative stability settings.

Prepare and review the credentials, then run preflight before the first pull:

```bash
cd deploy/arm64
cp palworld.env.example palworld.env
chmod 600 palworld.env
editor palworld.env
./preflight.sh
docker compose up -d
docker compose logs -f palworld
```

Only UDP 8211 is published. REST remains container-private and is used by the
health check; RCON is disabled. The complete `/palworld` tree is retained in
the `palworld-arm64-data` volume and the peer's protected scheduled backups are
enabled. Back up that volume before an image or game update. Never use
`docker compose down --volumes` unless world deletion is intentional.

The image is intentionally digest-pinned. Upgrading requires a new source,
manifest and ARM64 process validation; do not replace it with `latest`.
