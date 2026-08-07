# Browser bootstrap lab validation

Validation date: 2026-07-16. Production host and production Palworld service:
not used, changed, restarted or contacted.

## Boundary under test

The first-run service is a one-shot root process that accepts only loopback bind
addresses. Its static UI requires a 256-bit ephemeral launch code in a request
header. The code is printed only to the launching terminal; it is absent from
URLs, cookies, configuration and receipts. API responses use no-store, a
self-only Content Security Policy, no-referrer, no-sniff and frame-denial
headers. An unauthenticated status request returned 401.

The UI supports fresh SteamCMD installation and existing-tree adoption. Both
paths normalize non-overlapping absolute managed paths, validate distinct
bounded ports and host prerequisites, and create a server-held 15-minute plan.
Execution is bound to its random identifier and SHA-256 fingerprint, independent
browser-generated credentials, and an exact mode-specific confirmation. An
existing environment is never overwritten. Installer output is bounded and both
credentials are redacted before it can reach the browser.

Headless Chromium exercised the real unlock, configure and plan APIs at 1440px
and 390px. Desktop and mobile review screens had no horizontal overflow, plan
execution remained disabled for missing prerequisites, and the stage focus
returned to the top of each transaction step. JavaScript creates credentials
with `crypto.getRandomValues`; the API never returns them.

## Root adoption and service handoff: passing

An isolated privileged Ubuntu 24.04 container ran systemd 255 with a dedicated
backup volume, read-only source checkout and private network namespace. The
fixture existing tree contained regular executable launcher/server files, a
Steam appmanifest reporting build `24181105`, current/default settings, and one
world with one player save. A local alias stood in for the off-host rclone
configuration; no production or personal remote was mounted.

The real loopback HTTP API:

- refused an unauthenticated request with 401;
- reported every required host prerequisite present;
- hashed the critical server tree, inventoried one world/player and returned a
  review plan that explicitly did not reinstall game files;
- accepted only `ADOPT EXISTING INSTALL` for the reviewed plan;
- atomically created the environment as mode 0600 before the installer narrowed
  final service access to `root:palworld` mode 0640;
- preserved the pre-adoption settings as the managed raw base;
- installed the toolkit, services, timers, sudo boundary and static assets;
- completed with installer status 0 and a mode-0640, secret-free bootstrap
  receipt; and
- left the fake managed world, private operations console, REST/RCON firewall
  and disabled hairpin service active, 14 Palworld timers installed, and only
  loopback listeners on the bootstrap and operations ports.

The fixture launcher intentionally remained alive like a service but did not
simulate Palworld REST/game behavior; those behaviors have separate exact-image
and live-host evidence. The alias remote's empty-directory probe warned and
continued, so this lab does not replace the offsite-backup evidence.

After the first successful handoff, a second exact installer invocation detected
the adoption receipt, reverified its fingerprint and launcher, server, defaults
and appmanifest hashes, skipped the adoption mutation and exited 0. Automated
tests separately prove that a changed critical file blocks this resume path, a
failed browser install accepts only the same reviewed plan and credential pair,
and a retry can continue after the original plan time window without rewriting
the environment.

The disposable container, volume, browser process, fixture credentials and
temporary files were removed after validation.

If the one-shot HTTP process itself is lost, the atomic environment remains the
recovery boundary: the operator resumes `install.sh` from the root terminal. A
fresh install is idempotent; adoption is replanned if no receipt exists or
hash-verifies the existing receipt before continuing. The browser deliberately
does not redisplay persisted credentials.
