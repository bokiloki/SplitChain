import json
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from splitchain import public_status


def test_public_endpoint_restricts_methods_and_paths(monkeypatch):
    calls = []

    def fetch(method):
        calls.append(method)
        return {"source": "primary", "result": {"ok": True}}

    monkeypatch.setattr(public_status, "fetch_read_only", fetch)
    server = HTTPServer(("127.0.0.1", 0), public_status.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
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
