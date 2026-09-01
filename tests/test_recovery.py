import pytest

from splitchain.finality import FinalityRole
from splitchain.identity import CertificateAuthority
from splitchain.model import ProtocolError
from splitchain.recovery import FailureKind, RecoveryCoordinator, RecoverySigner


def setup_recovery():
    authority = CertificateAuthority("recovery-ca", "ca-secret")
    signers = {}
    for name in ("primary", "secondary", "tertiary", "observer-a", "observer-b"):
        certificate = authority.issue(name, f"{name}-public", ("observer",), 0, 100)
        signers[name] = RecoverySigner(certificate, f"{name}-secret")
    recovery = RecoveryCoordinator(
        authority,
        "branch-1",
        {
            FinalityRole.PRIMARY: "primary",
            FinalityRole.SECONDARY: "secondary",
            FinalityRole.TERTIARY: "tertiary",
        },
    )
    return recovery, signers


def claim(recovery, signer, role, round_number, evidence="evidence-a"):
    proof = signer.claim(
        role, "branch-1", round_number, FailureKind.NO_REVEAL, evidence
    )
    return recovery.submit_claim(
        proof, signer.certificate, signer.verification_key
    )


def open_challenge(recovery, signers, role, round_number):
    assert claim(recovery, signers["observer-a"], role, round_number) is False
    assert claim(recovery, signers["observer-b"], role, round_number) is True


def test_two_of_three_failure_claims_open_counterproof_window():
    recovery, signers = setup_recovery()
    open_challenge(recovery, signers, FinalityRole.PRIMARY, 4)
    assert recovery.pending_deadline == 7
    assert recovery.active_role == FinalityRole.PRIMARY


def test_valid_counterproof_prevents_takeover():
    recovery, signers = setup_recovery()
    open_challenge(recovery, signers, FinalityRole.PRIMARY, 4)
    proof = signers["primary"].counterproof(
        FinalityRole.PRIMARY, "branch-1", 4, "valid-reveal"
    )
    recovery.submit_counterproof(
        proof, signers["primary"].certificate, signers["primary"].verification_key, 6
    )
    assert recovery.active_role == FinalityRole.PRIMARY
    assert recovery.pending_deadline is None
    assert len(recovery.disputed) == 1


def test_primary_then_secondary_then_tertiary_takeover():
    recovery, signers = setup_recovery()
    open_challenge(recovery, signers, FinalityRole.PRIMARY, 1)
    assert recovery.advance(5) == FinalityRole.SECONDARY
    open_challenge(recovery, signers, FinalityRole.SECONDARY, 6)
    assert recovery.advance(10) == FinalityRole.TERTIARY


def test_all_roles_failed_aborts_safely():
    recovery, signers = setup_recovery()
    for role, start, expiry in (
        (FinalityRole.PRIMARY, 1, 5),
        (FinalityRole.SECONDARY, 6, 10),
        (FinalityRole.TERTIARY, 11, 15),
    ):
        open_challenge(recovery, signers, role, start)
        result = recovery.advance(expiry)
    assert result is None
    assert recovery.aborted is True


def test_conflicting_reporter_is_quarantined():
    recovery, signers = setup_recovery()
    claim(recovery, signers["observer-a"], FinalityRole.PRIMARY, 1)
    with pytest.raises(ProtocolError, match="conflicting failure evidence"):
        claim(
            recovery,
            signers["observer-a"],
            FinalityRole.PRIMARY,
            1,
            evidence="different-evidence",
        )
    assert recovery.quarantined == {"observer-a"}
