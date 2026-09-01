from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_compose_is_bounded_local_and_persistent():
    compose = (ROOT / "compose.yaml").read_text()
    assert compose.count('ports: ["127.0.0.1:') == 3
    assert compose.count("mem_limit: 256m") == 3
    assert compose.count("cpus: 0.50") == 3
    assert compose.count("pids_limit: 128") == 3
    assert compose.count("healthcheck:") == 3
    assert compose.count("restart: unless-stopped") == 3
    assert compose.count("/var/lib/splitchain/ledger.json") == 3


def test_container_runs_unprivileged_with_writable_state_directory():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "chown 65532:65532 /var/lib/splitchain" in dockerfile
    assert "USER 65532:65532" in dockerfile
