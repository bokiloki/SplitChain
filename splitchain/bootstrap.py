"""Pinned HTTPS discovery for valueless SplitChain sandbox clients."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

from .model import GenesisConfig, ProtocolError


def manifest(base: str, genesis: GenesisConfig) -> dict:
    address = urlsplit(base)
    if (address.scheme != "https" or not address.hostname or address.username
            or address.password or address.query or address.fragment or not address.path.endswith("/")):
        raise ProtocolError("bootstrap address must be an HTTPS origin or URL ending in /")
    return {
        "schema": "splitchain-bootstrap/v1",
        "network_id": genesis.network_id,
        "genesis_digest": genesis.digest(),
        "manifest_url": urljoin(base, ".well-known/splitchain-testnet.json"),
        "genesis_url": urljoin(base, "genesis.json"),
        "status_url": urljoin(base, "status"),
        "rpc_url": f"wss://{address.netloc}{address.path}rpc",
        "units": "valueless-testnet",
    }


def _fetch_json(url: str) -> dict:
    try:
        with urlopen(url, timeout=5) as response:
            if response.geturl() != url:
                raise ProtocolError("bootstrap URL redirected")
            body = response.read(64 * 1024 + 1)
            if len(body) > 64 * 1024:
                raise ProtocolError("bootstrap document too large")
            document = json.loads(body)
            if not isinstance(document, dict):
                raise ProtocolError("bootstrap document must be an object")
            return document
    except (OSError, ValueError) as exc:
        raise ProtocolError("unable to fetch bootstrap document") from exc


def join(base: str, local_genesis: str | Path) -> dict:
    """Trust the reviewed local genesis; reject a different remote network."""

    try:
        local = GenesisConfig.from_dict(json.loads(Path(local_genesis).read_text()))
    except (OSError, ValueError) as exc:
        raise ProtocolError("unable to load local genesis") from exc
    expected = manifest(base, local)
    remote = _fetch_json(expected["manifest_url"])
    if remote != expected:
        raise ProtocolError("bootstrap manifest differs from pinned genesis or endpoints")
    published = GenesisConfig.from_dict(_fetch_json(expected["genesis_url"]))
    if published.public() != local.public() or published.digest() != local.digest():
        raise ProtocolError("published genesis differs from pinned local genesis")
    return expected