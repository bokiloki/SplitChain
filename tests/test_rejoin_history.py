import pytest

from splitchain.model import ProtocolError
from splitchain.rejoin import RejoinHistory


def test_history_exports_only_events_after_checkpoint(tmp_path):
    history = RejoinHistory(tmp_path / "history.json", "genesis", "epoch")
    history.append({"method": "round", "position": 1})
    history.append({"method": "round", "position": 2})
    restored = RejoinHistory(tmp_path / "history.json", "genesis", "epoch")
    assert [event.message for event in restored.export_since(1)] == [
        {"method": "round", "position": 2}
    ]
    with pytest.raises(ProtocolError):
        restored.export_since(3)
