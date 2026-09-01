import pytest

from splitchain.finality import FinalityRole, FinalitySigner, ThreeRoundFinality
from splitchain.identity import CertificateAuthority
from splitchain.model import ProtocolError


def make_finality():
    authority = CertificateAuthority("local-finality-ca", "ca-secret")
    signers = {}
    roles = (
        FinalityRole.PRIMARY,
        FinalityRole.SECONDARY,
        FinalityRole.TERTIARY,
    )
    for role in roles:
        name = role.value
        certificate = authority.issue(name, f"{name}-public", ("observer",), 0, 10)
        signers[name] = FinalitySigner(certificate, role, f"{name}-secret")
    finality = ThreeRoundFinality(
        authority,
        "branch-1",
        "candidate-a",
        {name: signer.role for name, signer in signers.items()},
    )
    return finality, signers


def acknowledge(finality, signer, round_number, candidate="candidate-a"):
    vote = signer.vote("branch-1", candidate, round_number)
    return finality.acknowledge(
        vote, signer.certificate, signer.verification_key
    )


def test_two_of_three_acknowledgements_advance_each_round():
    finality, signers = make_finality()
    for round_number in (1, 2, 3):
        assert acknowledge(finality, signers["primary"], round_number) is False
        result = acknowledge(finality, signers["secondary"], round_number)
    assert result is True
    assert finality.finalized is True
    assert finality.status()["votes"]["3"] == ["primary", "secondary"]


def test_one_observer_cannot_advance_finality():
    finality, signers = make_finality()
    acknowledge(finality, signers["primary"], 1)
    assert finality.current_round == 1
    assert finality.finalized is False


def test_identical_duplicate_is_idempotent():
    finality, signers = make_finality()
    vote = signers["primary"].vote("branch-1", "candidate-a", 1)
    finality.acknowledge(vote, signers["primary"].certificate, signers["primary"].verification_key)
    finality.acknowledge(vote, signers["primary"].certificate, signers["primary"].verification_key)
    assert finality.status()["votes"]["1"] == ["primary"]


def test_conflicting_candidate_quarantines_observer():
    finality, signers = make_finality()
    with pytest.raises(ProtocolError, match="conflicting candidate"):
        acknowledge(finality, signers["primary"], 1, candidate="candidate-b")
    assert finality.quarantined == {"primary"}


def test_future_round_vote_is_rejected():
    finality, signers = make_finality()
    with pytest.raises(ProtocolError, match="current finality round"):
        acknowledge(finality, signers["primary"], 2)
