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


def test_worker_lifecycle(tmp_path):
    credentials = {"workers": {"olc-worker-001": "w" * 40}, "operator": "o" * 40}
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
        assert receipt == {"job_id": job["job_id"], "state": "verified",
                           "verification": "coordinator-sha256"}
        assert call("/worker/olc-worker-001/job", "w" * 40)["job_id"] == second["job_id"]
        assert Store(tmp_path / "state.json", credentials).operator_jobs()["jobs"][0]["state"] == "verified"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_bad_result_is_rejected(tmp_path):
    store = Store(tmp_path / "state.json", {"workers": {"olc-worker-001": "w" * 40},
                                           "operator": "o" * 40})
    job = store.enqueue()
    store.lease("olc-worker-001")
    assert store.result("olc-worker-001", job["job_id"], "0" * 64)["state"] == "rejected"


def test_separate_verifier_attestation(tmp_path):
    key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "verifier-private.pem"
    private_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    key = load_key(str(private_path))
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    store = Store(tmp_path / "state.json", {"workers": {"olc-worker-001": "w" * 40},
                                           "operator": "o" * 40,
                                           "verifier": {"token": "v" * 40, "public_key": public}})
    job = store.enqueue()
    store.lease("olc-worker-001")
    store.result("olc-worker-001", job["job_id"], EXPECTED)
    statement = store.verifier_job()
    assert statement["job_id"] == job["job_id"]
    statement["accepted"] = True
    bad_signature = "00" * 64
    try:
        store.attest(statement, bad_signature)
        assert False, "invalid signature accepted"
    except ValueError:
        pass
    signature = key.sign(canonical_json(statement)).hex()
    assert store.attest(statement, signature)["attestation"] == "ed25519-verifier-v1"
    assert store.verifier_job() == {"job": None}
    saved = Store(tmp_path / "state.json", store.credentials).operator_jobs()["jobs"][0]
    assert saved["attestation"] == {"statement": statement, "signature": signature}
    public_receipts = store.public_receipts()
    assert public_receipts["verifier_public_key"] == public
    assert public_receipts["receipts"][0]["attestation"]["signature"] == signature
    assert "token" not in json.dumps(public_receipts)
    assert verify_document(public_receipts, public) == (1, 0)
    try:
        verify_document(public_receipts, "0" * 64)
        assert False, "wrong pinned key accepted"
    except ValueError:
        pass
