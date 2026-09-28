import pytest

from splitchain.round_clock import next_round_delay


@pytest.mark.parametrize("step_seconds,expected_start_interval", [
    (0.1, 10),
    (3, 10),
    (9.9, 10),
    (10, 20),
    (13, 23),
])
def test_round_cadence_follows_monotonic_start_time_without_catchup_burst(
    step_seconds, expected_start_interval,
):
    started_at = 100.0
    completed_at = started_at + step_seconds
    next_start = completed_at + next_round_delay(started_at, completed_at)
    assert next_start - started_at == pytest.approx(expected_start_interval)


def test_round_cadence_rejects_invalid_clock_samples():
    with pytest.raises(ValueError):
        next_round_delay(10, 9)
    with pytest.raises(ValueError):
        next_round_delay(0, 1, interval=0)
