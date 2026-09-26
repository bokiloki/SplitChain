import pytest

from splitchain.auth import RequestAuthenticator
from splitchain.model import ProtocolError


def test_invalid_signature_and_replayed_nonce_have_same_error():
    auth = RequestAuthenticator({"alice": "secret"})
    request = {"id": "one", "method": "offer", "params": {}}
    request["auth"] = RequestAuthenticator.sign(request, "alice", 1, "secret")
    assert auth.verify(request) == "alice"

    with pytest.raises(ProtocolError) as replay:
        auth.verify(request)

    forged = dict(request)
    forged["auth"] = {**request["auth"], "signature": "0" * 64}
    with pytest.raises(ProtocolError) as invalid:
        auth.verify(forged)

    assert str(replay.value) == str(invalid.value) == "invalid request authentication"
    assert auth.snapshot() == {"alice": 1}
