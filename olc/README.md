# OLC Worker01: first machine check

This is the first step toward an OLC worker on a spare PC. The current
repository has a pinned testnet bootstrap client and local DistOPS/TrueLies
reference code. The optional fixed-job OLC pilot has an authenticated worker
heartbeat, job delivery, isolated execution, and public worker status. It does
**not** provide general workloads, TrueLies verifier networking, SplitChain
settlement, or consensus membership. Running the preflight check alone does
not enroll a node or make it an observer in the consensus protocol.

## On the spare machine

Install Debian with SSH server and standard system utilities, then run as your
ordinary user (not root):

```bash
sudo apt update
sudo apt install -y git python3 python3-venv pciutils util-linux
git clone https://github.com/bokiloki/SplitChain.git
cd SplitChain
python3 -m venv .venv
.venv/bin/pip install -e .
PATH="$PWD/.venv/bin:$PATH" bash olc/worker01-preflight.sh
```

Only clone from the expected project account. Inspect scripts before running
them. The check prints LAN addresses and hardware inventory; review its output
before sharing it. HTTPS verification remains enabled. Set `OLC_TESTNET_URL`
if your deployed manifest is at a different base path.

## Local sandbox smoke test

On Debian 13 install `podman uidmap slirp4netns fuse-overlayfs`, then run the
following as the ordinary `bokiloki` user without `sudo`:

```bash
bash olc/smoke-job.sh
```

The test pulls the official Debian 13 image and computes SHA-256 inside a
rootless container with one CPU, 256 MiB of RAM, no network, a read-only root
filesystem, dropped capabilities, and a 32-process limit. It checks the exact
expected hash and exits nonzero if the workload fails. It does not enroll the
machine or accept remote jobs.

## Next implementation milestone

After proving the fixed-job pilot, implement authenticated enrollment for
additional workers, per-job signed manifests, independent TrueLies verifier
nodes, and SplitChain settlement. Keep consensus participation disabled until
those paths have been tested independently.

The fixed-job outbound pilot is documented in
[`docs/OLC_WORKER01_PILOT.md`](../docs/OLC_WORKER01_PILOT.md).
