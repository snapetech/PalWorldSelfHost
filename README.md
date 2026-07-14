# PalWorldSelfHost

Production-oriented Palworld dedicated-server hosting for Linux. It installs
the official SteamCMD server, manages it with systemd, performs graceful nightly
maintenance, creates verified local and rclone backups, and includes private
operations and public read-only status pages.

## Features

- Native SteamCMD installation with automatic updates
- systemd service with restart-on-failure
- Graceful save, announcement, shutdown, update, backup, and restart workflow
- Nightly maintenance targeting 05:00 local time
- SHA-256 and archive verification before offsite upload
- Configurable rclone destination and optional LVM enforcement
- Ten-minute service, REST, listener, and backup-freshness health checks
- Loopback-only operations console with save, backup, and restart actions
- Public player count, uptime, maintenance schedule, and live-coordinate map
- No public exposure of player IPs, platform identifiers, REST credentials, or
  administrative endpoints

The example configuration uses a 50% global XP rate and no death drops. Change
those values for your own community. Palworld exposes one shared `ExpRate`; it
does not provide separate stock player and Pal XP multipliers.

## Requirements

- A modern Linux distribution with systemd
- `steamcmd`, `rclone`, `zstd`, Python 3, `curl`, and standard GNU utilities
- Root access for installation
- An existing rclone remote for offsite backups
- UDP 8211 forwarded to the server if it should be internet-accessible

Keep TCP 8212 (Pocketpair REST) and TCP 8213 (operations console) private.

## Install

```bash
git clone https://github.com/snapetech/PalWorldSelfHost.git
cd PalWorldSelfHost
sudo install -o root -g root -m 0600 \
  config/palworld-server.env.example /etc/palworld-server.env
sudo editor /etc/palworld-server.env
sudo ./scripts/install.sh
```

Generate independent, long random values for `PALWORLD_ADMIN_PASSWORD` and
`PALWORLD_OPS_TOKEN`. Configure the rclone remote before installation and never
commit the resulting environment or rclone configuration.

The installer creates the locked-down `palworld` service account, installs the
server under `/srv/palworld/server`, and enables these units:

```text
palworld.service
palworld-maintenance.timer
palworld-health.timer
palworld-ops.service
```

Check the deployment with:

```bash
systemctl status palworld.service
systemctl list-timers 'palworld-*'
journalctl -u palworld.service -f
```

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
| `PALWORLD_ALERT_COMMAND` | Optional executable receiving a health-error string |

## Public status page

Static assets are installed to `PALWORLD_PUBLIC_DIR`. The public page requests
`/palworld/status.json`; route only that URL to the loopback operations service:

```caddyfile
handle /palworld/status.json {
    rewrite * /api/public-status
    reverse_proxy 127.0.0.1:8213
}

handle_path /palworld/* {
    root * /var/www/palworld
    file_server
}
```

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

To restore, stop `palworld.service`, verify the selected archive, extract it
into `PALWORLD_INSTALL_DIR`, restore ownership to `palworld:palworld`, and start
the service. Preserve the current world separately before any restore.

## Security model

- UDP 8211 is the only game port intended for router forwarding.
- REST and operations services bind privately.
- Public status JSON explicitly strips IP addresses and platform identifiers.
- Mutating console actions require a separate operator token.
- The service account receives sudo permission only for the packaged backup
  command and restarting `palworld.service`.

See [SECURITY.md](SECURITY.md) for vulnerability reporting guidance.

## License

Project code is MIT licensed. Bundled map material has separate attribution and
licensing described above.
