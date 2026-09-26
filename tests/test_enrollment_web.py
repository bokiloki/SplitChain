import asyncio
import base64
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
import websockets

from splitchain import public_status
from splitchain.auth import RequestAuthenticator
from splitchain.enrollment import EnrollmentError
from splitchain.enrollment_web import AdminHandler, EnrollmentStore, PublicHandler
from splitchain.model import GenesisConfig
from splitchain.node import ReferenceNode


def genesis():
    return GenesisConfig.from_dict(json.loads(
        (Path(__file__).parents[1] / "configs/testnet-genesis.json").read_text()))


def test_web_queue_approval_and_private_receipt(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    registry = tmp_path / "accounts.json"
    registry.write_text(json.dumps({"testnet_operator": "o" * 64}))
    registry.chmod(0o600)
    store = EnrollmentStore(queue, registry, genesis())
    document = {"schema": "splitchain-wallet-enrollment/v1", "network_id": genesis().network_id,
                "account": "sc12345678", "credential": "a" * 64}
    saved = store.submit(json.dumps(document).encode())
    assert saved["status"] == "pending"
    assert "credential" not in saved
    assert store.submit(json.dumps(document).encode()) == saved
    assert (queue / "sc12345678.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(EnrollmentError, match="already requested"):
        store.submit(json.dumps({**document, "credential": "b" * 64}).encode())
    assert store.check(json.dumps({"account": document["account"], "receipt": saved["receipt"]}).encode())["status"] == "pending"
    with pytest.raises(EnrollmentError, match="not found"):
        store.check(json.dumps({"account": document["account"], "receipt": "wrong"}).encode())

    async def fake_activate(actor, registry_file, config):
        assert actor == document["account"]
        assert json.loads(registry_file.read_text())[actor] == document["credential"]
        return {"status": "active"}

    monkeypatch.setattr("splitchain.enrollment_web.activate", fake_activate)
    assert store.approve(document["account"]) == "active"
    assert store.approve(document["account"]) == "active"
    assert store.check(json.dumps({"account": document["account"], "receipt": saved["receipt"]}).encode())["status"] == "active"
    assert not (queue / "sc12345678.wallet.json").exists()


def test_operator_dashboard_requires_password_and_same_origin(tmp_path, monkeypatch):
    store = EnrollmentStore(tmp_path / "queue", tmp_path / "registry", genesis())
    AdminHandler.store = store
    AdminHandler.password = "dashboard-private-password-more-than-32"
    monkeypatch.setattr(store, "approve", lambda actor: "active")
    server = ThreadingHTTPServer(("127.0.0.1", 0), AdminHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    auth = "Basic " + base64.b64encode(b"operator:dashboard-private-password-more-than-32").decode()
    try:
        with pytest.raises(HTTPError) as exc:
            urlopen(url)
        assert exc.value.code == 401
        with urlopen(Request(url, headers={"Authorization": auth})) as result:
            assert b"Pending wallet requests" in result.read()
        bad = Request(url + "/approve", data=urlencode({"account": "sc12345678"}).encode(),
                      headers={"Authorization": auth, "Origin": "https://evil.example"})
        with pytest.raises(HTTPError) as exc:
            urlopen(bad)
        assert exc.value.code == 403
        valid = Request(url + "/approve", data=urlencode({"account": "sc12345678"}).encode(),
                        headers={"Authorization": auth, "Origin": url})
        with urlopen(valid) as response:
            assert response.status == 200  # urllib follows the post/redirect/get.
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_node_loads_new_approved_actor_without_resetting_replay_state(tmp_path):
    registry = tmp_path / "accounts.json"
    registry.write_text(json.dumps({"testnet_operator": "o" * 64}))
    node = ReferenceNode({"alice": 100, "bob": 0}, auth_secrets={"testnet_operator": "o" * 64},
                         auth_secrets_path=registry)

    async def scenario():
        registration = {"id": 1, "method": "account.register", "params": {"account": "sc12345678"}}
        registration["auth"] = RequestAuthenticator.sign(registration, "testnet_operator", 1, "o" * 64)
        assert "result" in await node.dispatch(registration)
        registry.write_text(json.dumps({"testnet_operator": "o" * 64, "sc12345678": "a" * 64}))
        offer = {"id": 2, "method": "offer", "params": {"sender": "sc12345678", "receiver": "alice", "value": 1}}
        offer["auth"] = RequestAuthenticator.sign(offer, "sc12345678", 1, "a" * 64)
        assert "sender needs" in (await node.dispatch(offer))["error"]["message"]
        assert node.authenticator.knows("sc12345678")
        assert node.authenticator.snapshot()["testnet_operator"] == 1
    asyncio.run(scenario())


def test_public_status_only_proxies_enrollment_routes(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    store = EnrollmentStore(tmp_path / "queue", registry, genesis())
    PublicHandler.store = store
    PublicHandler.limiter.clear()
    enrollment_server = ThreadingHTTPServer(("127.0.0.1", 0), PublicHandler)
    status_server = ThreadingHTTPServer(("127.0.0.1", 0), public_status.Handler)
    monkeypatch.setattr(public_status, "ENROLLMENT_BACKEND",
                        f"http://127.0.0.1:{enrollment_server.server_port}")
    threads = [Thread(target=server.serve_forever, daemon=True)
               for server in (enrollment_server, status_server)]
    for thread in threads:
        thread.start()
    base = f"http://127.0.0.1:{status_server.server_port}"
    payload = {"schema": "splitchain-wallet-enrollment/v1", "network_id": genesis().network_id,
               "account": "sc87654321", "credential": "d" * 64}
    try:
        request = Request(base + "/enroll", method="POST", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            issued = json.load(response)
        assert issued["status"] == "pending"
        assert "credential" not in issued
        check = Request(base + "/enroll/check", method="POST", data=json.dumps({
            "account": issued["account"], "receipt": issued["receipt"]}).encode(),
            headers={"Content-Type": "application/json"})
        with urlopen(check) as response:
            assert json.load(response)["status"] == "pending"
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + "/rpc", method="POST", data=b"{}"))
        assert exc.value.code == 405
    finally:
        for server in (enrollment_server, status_server):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


def test_new_wallet_authenticates_on_both_replicas_without_restart(tmp_path):
    async def scenario():
        secrets = {"testnet_operator": "o" * 64, "alice": "a" * 64}
        registry = tmp_path / "accounts.json"
        registry.write_text(json.dumps(secrets))
        cluster_secret = "cluster-secret-at-least-32-characters"
        secondary = ReferenceNode(
            {"alice": 100}, state_path=tmp_path / "secondary.json", auth_secrets=secrets,
            auth_secrets_path=registry, node_id="secondary", role="secondary",
            cluster_secret=cluster_secret)
        async with websockets.serve(secondary.handler, "127.0.0.1", 0) as server:
            primary = ReferenceNode(
                {"alice": 100}, state_path=tmp_path / "primary.json", auth_secrets=secrets,
                auth_secrets_path=registry, node_id="primary", role="primary",
                cluster_secret=cluster_secret,
                peer_urls={"secondary": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"})

            async def signed(method, params, actor, nonce, secret):
                request = {"id": f"{actor}-{nonce}", "method": method, "params": params}
                request["auth"] = RequestAuthenticator.sign(request, actor, nonce, secret)
                return await primary.dispatch(request)

            assert "result" in await signed("account.register", {"account": "sc12345678"},
                                            "testnet_operator", 1, "o" * 64)
            offer = await signed("offer", {"sender": "alice", "receiver": "sc12345678", "value": 1},
                                 "alice", 1, "a" * 64)
            assert "result" in offer
            registry.write_text(json.dumps({**secrets, "sc12345678": "n" * 64}))
            result = await signed("accept", {"branch_id": offer["result"]["branch_id"],
                                             "receiver": "sc12345678"}, "sc12345678", 1, "n" * 64)
            assert result["result"]["state"] == "accepted"
            assert primary.ledger.snapshot() == secondary.ledger.snapshot()
            assert secondary.authenticator.knows("sc12345678")

    asyncio.run(scenario())
