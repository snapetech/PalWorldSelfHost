# Multi-instance runtime validation

Validation was performed on 2026-07-16 with two disposable local Compose
projects. Production was not contacted or modified.

## Artifact and isolation

Both instances ran Pocketpair's pinned official image:

```text
ghcr.io/pocketpairjp/palserver:v1.0.1.100619
sha256:0d293cafd503a91a6d11d71f7bf770ee0c3c5ecf37db988349b2c1758f4e9358
```

The manager created `alpha` and `beta` with distinct Compose project names,
bind-mounted `saved/` trees, generated world IDs and non-overlapping public
game/query plus loopback REST/RCON ports. Real `docker compose config --quiet`
accepted both generated projects. Creation rejects duplicate registry ports,
ports already bound on the host, unsafe names, invalid settings, placeholder
administrator credentials and malformed world IDs.

## Runtime outcome

Both actual PalServer processes became healthy concurrently and ran as UID/GID
999 after a root-only configuration bootstrap. Their REST endpoints returned
different world GUIDs matching the manager's generated IDs:

```text
alpha  5A6221921E78AF57F9C760D03EB66E09
beta   11738D3F9EAE5CD1F15E4A280C51865E
```

Stopping `alpha` left `beta` running and answering its independently mapped REST
port. Restarting `alpha` returned the same world GUID and retained a marker in
its own `Saved` tree. Neither lifecycle command addressed the other Compose
project.

Exact-confirmed deletion used `docker compose down --remove-orphans` without
`--volumes`, moved each complete instance directory under `retired/`, and
preserved separate `alpha` and `beta` save markers. The active registry was
empty afterward and no disposable project/container remained.

## Faults found and corrected

The runtime drill was not treated as passing until it exposed and corrected
three real faults:

- a copied `0500` entrypoint was unreadable by the official image user;
- file-backed Compose config permissions made a protected `0600` settings file
  unreadable, so Compose now bootstraps as root, installs mode-0600 UID/GID-999
  configuration, and launches PalServer unprivileged; and
- an attempted `-servername` selector changed the public server name without
  pinning the save. The shared Compose/Helm adapter now atomically maintains
  `DedicatedServerName` in `GameUserSettings.ini`, refuses ambiguous/mismatched
  worlds, and proves the same GUID after restart.

The REST save phase now has the same 30-second bound as native maintenance; a
failed eight-second trial fell back to the 120-second container stop cap and was
retained as negative evidence rather than counted as a pass.

Four focused manager tests cover two-instance creation, generated configuration,
port/settings/world-ID refusals, project-scoped lifecycle/status, exact-confirmed
preserving deletion, and failed-start registry rollback. The existing
portability tests also prove the shared Compose/Helm adapter remains identical,
installs the persistent world selector, preserves mode-0600 settings and runs
the game child unprivileged.
