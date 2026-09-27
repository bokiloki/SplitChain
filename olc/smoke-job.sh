#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  printf '%s\n' 'Run as an ordinary user, not root.' >&2
  exit 1
fi
if ! command -v podman >/dev/null; then
  printf '%s\n' 'Podman is required.' >&2
  exit 1
fi
if [[ "$(podman info --format '{{.Host.Security.Rootless}}')" != true ]]; then
  printf '%s\n' 'Rootless Podman is required.' >&2
  exit 1
fi

expected='b491ef665fb7e74241fbd1e9ee89982bd49273f1a516f0776f9191d3d42e211d  -'
actual="$(podman run --rm --network none --read-only --cap-drop all \
  --security-opt no-new-privileges --pids-limit 32 --cpus 1 --memory 256m \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m docker.io/library/debian:13 \
  sh -c 'printf "OLC Worker01 test job\n" | sha256sum')"
if [[ "$actual" != "$expected" ]]; then
  printf 'Unexpected job result: %s\n' "$actual" >&2
  exit 1
fi
printf 'OLC local sandbox job passed: %s\n' "$actual"
