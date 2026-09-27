#!/usr/bin/env bash
# Run as an ordinary user on the proposed Debian worker. Makes no system changes.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bootstrap_url="${OLC_TESTNET_URL:-https://bokiloki.ddns.net/splitchain/}"

printf '%s\n' '=== Host ==='
hostnamectl
printf '%s\n' '=== Network interfaces ==='
ip -br addr
printf '%s\n' '=== Processor ==='
lscpu
printf '%s\n' '=== Memory ==='
free -h
printf '%s\n' '=== Disks ==='
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
printf '%s\n' '=== PCI display and network devices ==='
lspci | grep -Ei 'VGA|3D|Display|Ethernet|Network' || true
printf '%s\n' '=== Virtualization ==='
if command -v systemd-detect-virt >/dev/null; then systemd-detect-virt || true; fi
printf '%s\n' '=== Docker availability ==='
if command -v docker >/dev/null; then docker --version; docker compose version || true; else printf '%s\n' 'Docker not installed'; fi
printf '%s\n' '=== Pinned SplitChain testnet bootstrap ==='
if command -v scplit >/dev/null; then
  scplit join-testnet --url "$bootstrap_url" --genesis "$repo_root/configs/testnet-genesis.json"
else
  printf '%s\n' 'Install the local repository first: python3 -m venv .venv && .venv/bin/pip install -e .'
  printf 'Then run: .venv/bin/scplit join-testnet --url %s --genesis configs/testnet-genesis.json\n' "$bootstrap_url"
fi
