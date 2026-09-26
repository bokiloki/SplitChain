import pytest

from splitchain.model import ProtocolError
from splitchain.rejoin import RejoinCheckpoint, RejoinEnvelope, RejoinQueue


def checkpoint():
    return RejoinCheckpoint("epoch", 4, 12, "ledger", "state")


def test_rejoin_queue_survives_restart_and_drains_only_matching_checkpoint(tmp_path):
    queue = RejoinQueue(tmp_path / "rejoin.json", checkpoint())
    first = queue.append({"method": "bet.commit", "round": 13})
    queue.append({"method": "clock.heartbeat", "round": 13})
    restored = RejoinQueue(tmp_path / "rejoin.json", checkpoint())
    assert restored.events == [first, restored.events[1]]
    with pytest.raises(ProtocolError, match="checkpoint"):
        restored.drain(RejoinCheckpoint("epoch", 5, 13, "other", "state"))
    assert restored.drain(checkpoint()) == [
        {"method": "bet.commit", "round": 13},
        {"method": "clock.heartbeat", "round": 13},
    ]


def test_rejoin_queue_rejects_forged_or_out_of_order_envelope(tmp_path):
    queue = RejoinQueue(tmp_path / "rejoin.json", checkpoint())
    event = RejoinEnvelope.create(2, "wrong-parent", {"method": "x"})
    with pytest.raises(ProtocolError, match="out of order"):
        queue.append_envelope(event)
