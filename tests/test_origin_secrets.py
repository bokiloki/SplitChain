import os

import pytest

from splitchain.model import ProtocolError
from splitchain.origin_secrets import OriginBetSecrets


def test_only_origin_key_can_open_persisted_reveal_material(tmp_path):
    key = os.urandom(32)
    origin = OriginBetSecrets(tmp_path / "origin", "primary", key)
    secret = "origin-only-bet-secret-with-at-least-32-characters"
    origin.save(8, "epoch-a", secret)
    assert secret.encode() not in (tmp_path / "origin" / "bet-8.sealed").read_bytes()
    assert origin.read(8, "epoch-a") == secret
    with pytest.raises(ProtocolError, match="unavailable"):
        OriginBetSecrets(tmp_path / "origin", "secondary", key).read(8, "epoch-a")
    with pytest.raises(ProtocolError, match="unavailable"):
        OriginBetSecrets(tmp_path / "origin", "primary", os.urandom(32)).read(8, "epoch-a")
    with pytest.raises(ProtocolError, match="unavailable"):
        origin.read(8, "epoch-b")
    with pytest.raises(ProtocolError, match="already exists"):
        origin.save(8, "epoch-a", "another-secret-with-at-least-32-characters")
