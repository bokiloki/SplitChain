"""Exercise authenticated OLC job lifecycle and persistence."""

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.model import canonical_json
from splitchain.olc_gateway import EXPECTED, Handler, Store
from splitchain.olc_receipts import verify_document
from splitchain.olc_verifier import load_key


def quorum_credentials():
    keys = {f"verifier-{i}": Ed25519PrivateKey.generate() for i in (1, 2, 3)}
    credentials = {"workers": {"olc-worker-001": "w" * 40}, "operator": "o" * 40,
                   "verifiers": {name: {"token": str(i) * 40,
                       "public_key": key.public_key().public_bytes(
                           serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()}
                       for i, (name, key) in enumerate(keys.items(), 1)}}
    return credentials, keys


def test_worker_lifecycle(tmp_path):
    credentials, keys = quorum_credentials()
    Handler.store = Store(tmp_path / "state.json", credentials)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(path, token=None, body=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(f"http://127.0.0.1:{server.server_port}{path}",
                          headers=headers, data=json.dumps(body).encode() if body is not None else None)
        with urlopen(request) as response:
            return json.load(response)

    try:
        try:
            call("/worker/olc-worker-001/job")
            assert False, "unauthenticated job lease succeeded"
        except HTTPError as error:
            assert error.code == 401
        assert call("/worker/olc-worker-001/heartbeat", "w" * 40,
                    {"capabilities": {"cpu_threads": 16, "memory_mb": 31744}})["accepted"]
        assert call("/workers")["workers"][0]["state"] == "online"
        try:
            call("/operator/job", "w" * 40,
                 {"kind": "sha256-fixed-v1", "node_id": "olc-worker-001"})
            assert False, "worker token submitted an operator job"
        except HTTPError as error:
            assert error.code == 401
        job = call("/operator/job", "o" * 40,
                   {"kind": "sha256-fixed-v1", "node_id": "olc-worker-001"})
        leased = call("/worker/olc-worker-001/job", "w" * 40)
        assert leased["job_id"] == job["job_id"]
        second = call("/operator/job", "o" * 40,
                      {"kind": "sha256-fixed-v1", "node_id": "olc-worker-001"})
        assert call("/worker/olc-worker-001/job", "w" * 40) == {"job": None}
        receipt = call("/worker/olc-worker-001/result", "w" * 40,
                       {"job_id": job["job_id"], "digest": EXPECTED})
        assert receipt == {"job_id": job["job_id"], "state": "awaiting_quorum",
                           "verification": "coordinator-sha256"}
        try:
            call("/verifier/verifier-2/job", "1" * 40)
            assert False, "one verifier impersonated another"
        except HTTPError as error:
            assert error.code == 401
        for verifier_id in ("verifier-1", "verifier-2"):
            token = verifier_id[-1] * 40
            statement = call(f"/verifier/{verifier_id}/job", token)
            statement.update(accepted=True, verifier_id=verifier_id)
            signature = keys[verifier_id].sign(canonical_json(statement)).hex()
            vote = call(f"/verifier/{verifier_id}/attest", token,
                        {"statement": statement, "signature": signature})
        assert vote["state"] == "quorum_verified"
        assert call("/worker/olc-worker-001/job", "w" * 40)["job_id"] == second["job_id"]
        assert Store(tmp_path / "state.json", credentials).operator_jobs()["jobs"][0]["state"] == "quorum_verified"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_bad_result_is_rejected(tmp_path):
    credentials, _ = quorum_credentials()
    store = Store(tmp_path / "state.json", credentials)
    job = store.enqueue()
    store.lease("olc-worker-001")
    assert store.result("olc-worker-001", job["job_id"], "0" * 64)["state"] == "rejected"


def test_separate_verifier_attestation(tmp_path):
    credentials, keys = quorum_credentials()
    key = keys["verifier-1"]
    private_path = tmp_path / "verifier-private.pem"
    private_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    key = load_key(str(private_path))
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    credentials["verifier"] = {"token": "v" * 40, "public_key": public}
    store = Store(tmp_path / "state.json", credentials)
    job = store.enqueue()
    store.lease("olc-worker-001")
    store.result("olc-worker-001", job["job_id"], EXPECTED)
    statement = store.verifier_job("verifier-1")
    assert statement["job_id"] == job["job_id"]
    statement["accepted"] = True
    statement["verifier_id"] = "verifier-1"
    bad_signature = "00" * 64
    try:
        store.attest("verifier-1", statement, bad_signature)
        assert False, "invalid signature accepted"
    except ValueError:
        pass
    signature = key.sign(canonical_json(statement)).hex()
    assert store.attest("verifier-1", statement, signature)["state"] == "awaiting_quorum"
    assert store.verifier_job("verifier-1") == {"job": None}
    second_statement = store.verifier_job("verifier-2")
    second_statement.update(accepted=True, verifier_id="verifier-2")
    second_signature = keys["verifier-2"].sign(canonical_json(second_statement)).hex()
    assert store.attest("verifier-2", second_statement, second_signature)["state"] == "quorum_verified"
    assert store.verifier_job("verifier-3") == {"job": None}
    saved = Store(tmp_path / "state.json", store.credentials).operator_jobs()["jobs"][0]
    assert saved["attestations"]["verifier-1"] == {"statement": statement, "signature": signature}
    public_receipts = store.public_receipts()
    assert public_receipts["verifier_public_key"] == public
    assert public_receipts["receipts"][0]["attestations"]["verifier-1"]["signature"] == signature
    assert "token" not in json.dumps(public_receipts)
    assert verify_document(public_receipts, public) == (1, 0)
    try:
        verify_document(public_receipts, "0" * 64)
        assert False, "wrong pinned key accepted"
    except ValueError:
        pass


def test_single_vote_cannot_finalize_and_legacy_receipts_survive(tmp_path):
    credentials, keys = quorum_credentials()
    store = Store(tmp_path / "state.json", credentials)
    job = store.enqueue()
    store.lease("olc-worker-001")
    store.result("olc-worker-001", job["job_id"], EXPECTED)
    vote = store.verifier_job("verifier-1")
    vote.update(accepted=True, verifier_id="verifier-1")
    store.attest("verifier-1", vote, keys["verifier-1"].sign(canonical_json(vote)).hex())
    assert store.operator_jobs()["jobs"][0]["state"] == "awaiting_quorum"
    assert verify_document(store.public_receipts()) == (0, 1)
    # The old pilot's completed receipts retain their historical single-verifier state.
    store.data["jobs"]["legacy"] = {"node_id": "olc-worker-001", "state": "verified",
                                    "result_digest": EXPECTED}
    store._save()
    assert Store(tmp_path / "state.json", credentials).operator_jobs()["jobs"][-1]["state"] == "verified"


def test_two_denials_dispute_and_replay_is_rejected(tmp_path):
    credentials, keys = quorum_credentials()
    store = Store(tmp_path / "state.json", credentials)
    job = store.enqueue()
    store.lease("olc-worker-001")
    store.result("olc-worker-001", job["job_id"], EXPECTED)
    for verifier_id in ("verifier-1", "verifier-2"):
        statement = store.verifier_job(verifier_id)
        statement.update(accepted=False, verifier_id=verifier_id)
        signature = keys[verifier_id].sign(canonical_json(statement)).hex()
        receipt = store.attest(verifier_id, statement, signature)
    assert receipt["state"] == "disputed"
    assert store.verifier_job("verifier-3") == {"job": None}
    assert verify_document(store.public_receipts()) == (0, 1)
    wrong = dict(statement, verifier_id="verifier-3")
    try:
        store.attest("verifier-3", wrong, signature)
        assert False, "a signature was replayed as another verifier"
    except ValueError:
        pass
