import json
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from splitchain import public_status
from splitchain.bootstrap import manifest
from splitchain.model import GenesisConfig


def test_public_endpoint_restricts_methods_and_paths(monkeypatch):
    calls = []

    def fetch(method):
        calls.append(method)
        return {"source": "primary", "result": {"ok": True}}

    monkeypatch.setattr(public_status, "fetch_read_only", fetch)
    genesis_path = Path(__file__).parents[1] / "configs/testnet-genesis.json"
    monkeypatch.setenv("TESTNET_GENESIS_FILE", str(genesis_path))
    monkeypatch.setenv("TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/")
    server = HTTPServer(("127.0.0.1", 0), public_status.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url + "/") as response:
            assert b"Join with a pinned genesis" in response.read()
        with urlopen(url + "/downloads") as response:
            downloads = response.read()
            assert response.headers.get_content_type() == "text/html"
            assert b"SplitChain-Testnet-debug.apk" in downloads
            assert b"__SPLITCHAIN_BASE__" not in downloads
        with urlopen(url + "/wallet.js") as response:
            wallet_js = response.read()
            assert response.headers.get_content_type() == "text/javascript"
            assert b"__SPLITCHAIN_BASE__" not in wallet_js
            assert b"https://bokiloki.ddns.net/" in wallet_js
        for page in ("genesis", "status", "leadership", "bootstrap", "nodes"):
            with urlopen(url + "/explore/" + page) as response:
                body = response.read()
                assert response.headers.get_content_type() == "text/html"
                assert b"__SPLITCHAIN_BASE__" not in body
                assert b"https://bokiloki.ddns.net/" in body
        with urlopen(url + "/.well-known/splitchain-testnet.json") as response:
            genesis = GenesisConfig.from_dict(json.loads(genesis_path.read_text()))
            assert json.load(response) == manifest("https://bokiloki.ddns.net/", genesis)
        with urlopen(url + "/genesis.json") as response:
            assert json.load(response)["max_supply"] == 21_000_000
        with urlopen(url + "/status") as response:
            assert json.load(response)["source"] == "primary"
        with urlopen(url + "/leadership") as response:
            assert json.load(response)["result"]["ok"] is True
        for request, status in ((Request(url + "/status", method="POST", data=b"{}"), 405),
                                (Request(url + "/rpc"), 404),
                                (Request(url + "/status?method=offer"), 404)):
            try:
                urlopen(request)
                raise AssertionError("unsafe request unexpectedly succeeded")
            except HTTPError as error:
                assert error.code == status
        assert calls == ["status", "cluster.leadership"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_node_status_marks_missing_or_different_heads(monkeypatch):
    def probe(backend):
        name = backend.removeprefix("ws://").split(":")[0]
        return {"name": name, "state": "online", "height": 2,
                "digest": "same", "round": 5}

    monkeypatch.setattr(public_status, "fetch_node_status", probe)
    matching = public_status.fetch_nodes()
    assert matching["online"] == 3
    assert matching["heads_agree"] is True
    assert [node["name"] for node in matching["nodes"]] == [
        "primary", "secondary", "tertiary",
    ]

    def different(backend):
        node = probe(backend)
        if node["name"] == "tertiary":
            node["digest"] = "different"
        return node

    monkeypatch.setattr(public_status, "fetch_node_status", different)
    assert public_status.fetch_nodes()["heads_agree"] is False
