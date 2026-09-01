import pytest

from splitchain.distops import ComputeNode, DistOPS, Risk, Workload
from splitchain.ecosystem import Ecosystem
from splitchain.truelies import ServiceEvent, TrueLies


def test_full_ecosystem_vertical_slice():
    result = Ecosystem().demo()
    assert result["application"]["status"] == "completed"
    assert result["distops"]["sandbox"]["network"] == "deny"
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
