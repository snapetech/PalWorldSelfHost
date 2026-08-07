# Portability lab validation

Validation date: 2026-07-15/16. Production host: not used for container or
Kubernetes execution.

## Official container: passing

Artifact under test:

```text
ghcr.io/pocketpairjp/palserver:v1.0.1.100619
sha256:0d293cafd503a91a6d11d71f7bf770ee0c3c5ecf37db988349b2c1758f4e9358
linux/amd64
```

The registry manifest and 3,091-byte image configuration were inspected before
pulling. The configuration identifies the non-root `user` account and
Pocketpair's `/bin/sh /pal/Package/PalServer.sh` entrypoint. The digest-pinned
image pull completed with the expected registry digest.

The isolated Docker lab mounted a disposable `Saved` volume, the reviewed
adapter and a disposable INI. Evidence:

- the actual server reported `Game version is v1.0.1.100619`, opened REST 8212
  internally and ran the unpublished lab game port;
- the child-PID probe stayed live and the effective INI retained the supplied
  server name, description, public port and REST port;
- a generated `Level.sav` existed on the volume;
- a second container started from the same volume without any settings mount,
  retained the server name and generated world, and returned the expected REST
  version;
- stop performed authenticated REST save and shutdown, REST stopped, Docker
  completed in 12 seconds with exit 0 and `OOMKilled=false`; and
- the lab container and volume were removed afterward.

The lab found and corrected two real compatibility faults before passing:
Pocketpair ships `PalServer.sh` readable but not executable, and the script does
not `exec` the shipping binary. The adapter now invokes it through `/bin/sh`,
uses a separate process group, and prefers REST save/shutdown. A signal-only
trial exhausted the 120-second bound with exit 137 and is retained as negative
evidence; it was not treated as a passing graceful stop.

The later multi-instance runtime drill strengthened this adapter again. Local
file-backed Compose configs retain host permissions, so the service now runs a
root-only bootstrap that installs mode-0600 UID/GID-999 settings before dropping
PalServer to UID/GID 999. It also atomically pins `DedicatedServerName` in
`GameUserSettings.ini`; a two-world runtime proved distinct GUIDs, isolated
stop/restart and same-GUID persistence. REST save now receives 30 seconds before
the existing process-group fallback. See
[`multi-instance-lab-validation.md`](multi-instance-lab-validation.md).

Automated coverage in `tests/test_portability.py` proves malformed INI refusal,
atomic mode-0600 configuration install, signal fallback against a fixture,
child/ready cleanup, exact image pinning and Compose/Helm adapter identity.
`docker compose config`, `helm lint`, both Helm render modes, `sh -n` and
`shellcheck` pass.

## ARM64/Box64 path: passing contract and emulator chain

The supported ARM64 server-only Compose path directly pins the qualifying
`thijsvanloef/palworld-server-docker` peer at source commit
`5d4b3a1ab9ed24cb473d603f6666f6a40b9ee2c8` / release `v2.6.0` and OCI index
digest `sha256:85ae20d8756dd398ec24f4253a59b80907e5ffe411148a67b2c80c4527a8855e`.
The reviewed ARM64 manifest is
`sha256:e09c1e16db753e01113e0f8a1f7f8b3af46664e51646e3d265b5b72bec113218`.

Registry inspection proved that index contains distinct `linux/amd64` and
`linux/arm64` manifests. The ARM64 artifact was pulled by its manifest digest;
its `/usr/local/bin/box64-generic` is an AArch64 ELF and its startup path wraps
Palworld's x86-64 shipping binary with Box64. A disposable nested-emulation lab
then ran host QEMU AArch64 -> the artifact's ARM64 Box64 -> its x86-64 SteamCMD.
SteamCMD downloaded and installed its current 40,273-KiB update, launched and
exited successfully. No production host, save, network rule or service was
contacted.

The preflight refuses non-ARM64 hosts/daemons, any page size other than 4096,
missing Compose v2, a permissive/symlinked credential file, placeholders, admin
passwords shorter than 16 characters, unknown device profiles and changed
index/ARM manifest digests. Compose publishes only UDP 8211, keeps REST private
for health, disables RCON, persists all `/palworld` data and uses conservative
Box64 stability flags. Fixture tests cover preflight success and 16-KiB refusal.
CI repeats the exact Box64 -> x86-64 SteamCMD proof on a native
`ubuntu-24.04-arm` runner.

Confidence is **moderate** rather than high: this is the peer's own source- and
digest-pinned ARM64 implementation and the complete emulator chain is proven,
but ARM emulation remains slower and device-sensitive. The 4-KiB page gate and
explicit device profiles are part of the supported boundary, not suggestions.

## Kubernetes/Helm: passing

An ephemeral kind v0.31.0 / Kubernetes 1.35.0 cluster installed the Helm release
with the exact Pocketpair image named above preloaded under its registry digest.
The release was `deployed`; the single-replica StatefulSet reached Ready; both
Services were created; and its 20 GiB ReadWriteOnce claim bound through the
default StorageClass. The running container's reported image ID exactly matched
the reviewed registry digest.

Authenticated REST reported game version `v1.0.1.100619`, the configured lab
server name and description, and a generated world GUID. A non-empty active
`Level.sav` existed beneath that world's directory on the PVC.

Deleting the pod then exercised the real Kubernetes termination path. The old
pod completed in 5 seconds after its logs recorded successful REST save and
shutdown, REST termination, and adapter cleanup. The StatefulSet created a pod
with a different UID; it reached Ready with zero restarts, again reported the
exact image digest, and returned the same REST version, name, description and
world GUID. The PVC UID and bound PV name were identical before and after the
replacement, and the active non-empty `Level.sav` remained present. Its content
hash changed after the graceful save, as expected for a live world.

The lab used disposable credentials and storage and did not route traffic to,
change, restart or otherwise involve the production host. The ephemeral cluster
and its storage were removed after the evidence and automated validations were
captured.
