import pytest

from splitchain.distops import (
    ComputeNode,
    DistOPS,
    ManifestAuthority,
    Risk,
    Workload,
)
from splitchain.ecosystem import Ecosystem
from splitchain.truelies import ServiceEvent, TrueLies


def test_full_ecosystem_vertical_slice():
    result = Ecosystem().demo()
    assert result["application"]["status"] == "completed"
    assert result["distops"]["sandbox"]["network"]["mode"] == "deny"
    assert result["distops"]["manifest"]["issuer"] == "ecosystem-distops-ca"
    assert result["truelies"]["quorum"] is True
    assert result["finality"]["finalized"] is True
    assert result["finality"]["votes"]["3"] == ["primary", "secondary"]
    assert result["protocol"]["balances"] == {"alice": 990, "bob": 1010}
    assert len(result["nodes"]) == 3


def test_truelies_requires_two_of_three():
    layer = TrueLies()
    event = ServiceEvent.create("test", "alice", {"ok": True})
    assert layer.prove(event, [True, True, False])["quorum"] is True
    assert layer.prove(event, [True, False, False])["quorum"] is False


def test_untrusted_node_only_accepts_low_risk_work():
    distops = DistOPS([ComputeNode("edge", 4, 1024, 10, trusted=False)])
    low = Workload("splitchain/echo:0.1", ("echo", "ok"), risk=Risk.LOW)
    assert distops.schedule(low)["status"] == "completed"
    with pytest.raises(ValueError):
        distops.schedule(Workload("splitchain/hash:0.1", ("hash",), risk=Risk.HIGH))


def test_signed_manifest_binds_complete_workload():
    authority = ManifestAuthority("local-distops-ca", "manifest-secret")
    distops = DistOPS(
        [ComputeNode("trusted", 8, 4096, 90, trusted=True)],
        manifest_authority=authority,
    )
    workload = Workload(
        "splitchain/hash:0.1",
        ("hash", "payload"),
        risk=Risk.HIGH,
        network_allowlist=("storage.internal:443",),
        secret_names=("job-token",),
    )
    manifest = authority.issue(workload, 1, 5)
    receipt = distops.schedule(workload, manifest, current_round=2)
    assert receipt["sandbox"]["runtime"] == "microvm"
    assert receipt["sandbox"]["network"]["mode"] == "allowlist"
    assert receipt["sandbox"]["secrets"]["persisted"] is False
    assert receipt["completion_proof"] == receipt["result_digest"]


def test_manifest_rejects_modified_workload_and_expiry():
    authority = ManifestAuthority("local-distops-ca", "manifest-secret")
    distops = DistOPS(
        [ComputeNode("trusted", 8, 4096, 90, trusted=True)],
        manifest_authority=authority,
    )
    workload = Workload("splitchain/hash:0.1", ("hash", "original"), risk=Risk.HIGH)
    manifest = authority.issue(workload, 1, 5)
    changed = Workload("splitchain/hash:0.1", ("hash", "changed"), risk=Risk.HIGH)
    with pytest.raises(ValueError, match="manifest"):
        distops.schedule(changed, manifest, current_round=2)
    with pytest.raises(ValueError, match="manifest"):
        distops.schedule(workload, manifest, current_round=5)


def test_reputation_floor_is_risk_dependent():
    medium = Workload("splitchain/hash:0.1", ("hash",), risk=Risk.MEDIUM)
    with pytest.raises(ValueError, match="eligible"):
        DistOPS([ComputeNode("trusted-low-rep", 8, 4096, 49, trusted=True)]).schedule(medium)
