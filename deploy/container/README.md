# Official container deployment

This deployment runs Pocketpair's official, immutable `amd64` image. The tag
and registry manifest digest are both pinned. Palworld's complete `Saved` tree,
including worlds and the effective configuration, lives in a named volume.

Copy the example settings file, replace every placeholder, and start the world:

```bash
cd deploy/container
cp PalWorldSettings.ini.example PalWorldSettings.ini
chmod 600 PalWorldSettings.ini
editor PalWorldSettings.ini
docker compose config --quiet
docker compose up -d
docker compose logs -f palworld
```

The adapter validates and atomically copies the supplied settings into the
persistent volume before every start. Compose starts the adapter as root only
long enough to install mode-0600 UID/GID-999 settings and select a persistent
world in `GameUserSettings.ini`; PalServer itself runs as the official image's
unprivileged user. A sole existing world is recovered automatically, multiple
worlds require a valid existing selection, and mismatches fail closed. Docker stop requests a REST save and
shutdown using the private configured credentials, with a process-group
`SIGINT` fallback matching the native service. The 120-second grace period then
bounds shutdown. The Pocketpair REST port is intentionally not published.

Back up the `palworld-saved` volume before changing the image digest. A normal
`docker compose down` preserves it; `docker compose down --volumes` destroys it
and must not be used unless deletion is intentional.
