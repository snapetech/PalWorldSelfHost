# PalWorldSelfHost Helm deployment

This single-replica StatefulSet runs the pinned Pocketpair official `amd64`
image. A PVC holds the complete `Saved` tree. The chart exposes only the UDP
game port and keeps REST inside the pod network.

The chart deliberately starts with a reference to a Secret that does not exist.
Create it from the complete current `PalWorldSettings.ini` before installation;
this keeps credentials out of Helm values and release data:

```bash
kubectl create secret generic palworld-settings \
  --from-file=PalWorldSettings.ini=/secure/PalWorldSettings.ini
helm upgrade --install palworld ./deploy/helm/palworld-selfhost \
  --set settings.existingSecret=palworld-settings
```

After changing an externally managed settings Secret, restart the StatefulSet
so the adapter can atomically apply the new file.

The adapter validates and atomically copies that file onto the PVC before each
start. `SIGTERM` requests a private REST save/shutdown, with a process-group
`SIGINT` fallback; Kubernetes enforces the configured 120-second shutdown
bound. Startup/readiness/liveness probes follow the actual child PID.

Render and lint before applying:

```bash
helm lint deploy/helm/palworld-selfhost
helm template palworld deploy/helm/palworld-selfhost >/tmp/palworld.yaml
```

The Pocketpair image is x86-64 only. Do not schedule this chart on ARM64 nodes
without a separately validated emulation image and runtime class.

The registry digest is required by the shipped production values. Setting
`image.digest=""` is supported only for air-gapped registries or preloaded lab
images whose local registry rewrites the manifest; pin the equivalent artifact
in that registry's own controls.

## Validated deployment contract

On 2026-07-16, an isolated kind v0.31.0 / Kubernetes 1.35.0 lab proved the
shipped chart with the exact pinned Pocketpair digest. The pod reached Ready and
served the configured identity through authenticated private REST. Kubernetes
pod deletion completed in 5 seconds through REST save/shutdown, and the
replacement reached Ready with zero restarts, the same PVC/PV identity and the
same generated world GUID. See
[`docs/portability-lab-validation.md`](../../../docs/portability-lab-validation.md)
for the evidence boundary and limitations.
