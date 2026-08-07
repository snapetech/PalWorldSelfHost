# Isolated multi-instance Compose deployment

This supported deployment creates multiple Pocketpair official-image servers on
one Linux host. Every instance has its own `Saved` tree, settings, four host
ports, persistent 32-hex world ID, Compose project, health state and graceful lifecycle. The immutable image
digest and shutdown adapter are the same ones validated by the single-instance
container deployment.

Prepare a settings file for each instance. It must use the container-internal
ports `PublicPort=8211`, `RESTAPIPort=8212` and, when present,
`RCONPort=25575`; host-side ports are selected separately. Replace the example
administrator password first.

```bash
cp ../container/PalWorldSettings.ini.example ./friends.ini
editor ./friends.ini
chmod 600 ./friends.ini

./instance-manager.py --root ./state create friends --settings ./friends.ini \
  --game-port 8211 --query-port 27015 --rest-port 8212 --rcon-port 25575 --start
./instance-manager.py --root ./state create challenge --settings ./challenge.ini \
  --game-port 8221 --query-port 27025 --rest-port 8222 --rcon-port 25585 --start
./instance-manager.py --root ./state list
./instance-manager.py --root ./state status friends
./instance-manager.py --root ./state stop friends
./instance-manager.py --root ./state start friends
./instance-manager.py --root ./state restart friends
```

Game and query ports bind publicly. REST and RCON bind only to loopback. Creation
rejects duplicate managed ports and ports already bound on the host. Each
instance is a separate Compose project and bind-mounted `saved/` directory, so
starting or stopping one never addresses another.

Deletion requires exact confirmation and deliberately does not pass
`--volumes`. It stops/removes only that Compose project and moves the complete
instance directory—including `saved/`—under `state/retired/`:

```bash
./instance-manager.py --root ./state delete challenge --confirm 'DELETE INSTANCE'
```

Restoring a retired instance is explicit: inspect its settings and saves, choose
currently unused ports, then create a new instance and copy the preserved
`saved/` tree while the new project is stopped. Pass the preserved
`instance.json` `world_id` back with `--world-id`; the adapter refuses a mismatch
instead of silently starting a different world. Never run `docker compose down
--volumes` against these projects.
