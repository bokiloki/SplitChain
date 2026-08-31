from splitchain.simulator import run


def test_simulation_is_deterministic():
    first = run(seed=42, steps=100)
    second = run(seed=42, steps=100)
    assert first == second
    assert first.accepted_actions + first.rejected_actions == 100

