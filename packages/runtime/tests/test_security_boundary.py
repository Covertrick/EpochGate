import hashlib

import pytest

from epochgate_runtime.ledger import RunLedger
from epochgate_runtime.sandbox import (
    PUBLISHER_UID,
    container_boundary,
    docker_available,
    exec_in,
    exec_root,
    lock_workspace,
    publish_container,
    read_staging_bytes,
    start_container,
    stop_container,
    write_published_bytes,
)


pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker engine is not available")


def test_publisher_is_not_root_and_container_has_no_escape_mount():
    name = "eg-rt-boundary"
    start_container(name)
    ledger = RunLedger()
    try:
        lock_workspace(name)
        facts = container_boundary(name)
        assert facts["privileged"] is False
        assert facts["network_none"] is True
        assert facts["mount_count"] == 0
        assert facts["docker_socket_visible"] is False
        ledger.create_run("secure")
        ledger.advance("secure", "running")
        state = ledger.get_run("secure")
        staging = "/scratch/.eg-staging/owned"
        assert read_staging_bytes(name, staging) is None
        written = exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/scratch/.eg-staging/owned').write_bytes(b'placeholder')"])
        assert written.returncode == 0
        saved = b"safe-bytes"
        exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/scratch/.eg-staging/owned').write_bytes(b'safe-bytes')"])
        assert read_staging_bytes(name, staging) == saved
        exec_in(name, ["python", "-c", "import pathlib; pathlib.Path('/scratch/.eg-staging/owned').write_bytes(b'swapped')"])
        assert write_published_bytes(name, "kept.py", saved)
        assert exec_root(name, ["cat", "/workspace/kept.py"]).stdout == saved
        owner = exec_root(name, ["python", "-c", "import os; print(os.stat('/workspace/kept.py').st_uid)"])
        assert owner.stdout.decode().strip() == PUBLISHER_UID
        exec_in(name, ["python", "-c", "import os; os.symlink('/etc/passwd', '/scratch/.eg-staging/link')"])
        published = publish_container(
            ledger,
            container=name,
            run_id="secure",
            idempotency_key="link",
            fencing_token=state.fencing_token,
            relative_path="passwd.py",
            staging_path="/scratch/.eg-staging/link",
        )
        assert published["published"] is False
        missing = exec_in(name, ["python", "-c", "import pathlib; raise SystemExit(0 if pathlib.Path('/workspace/passwd.py').exists() else 1)"])
        assert missing.returncode == 1
        escaped = publish_container(
            ledger,
            container=name,
            run_id="secure",
            idempotency_key="escape",
            fencing_token=state.fencing_token,
            relative_path="../kept.py",
            staging_path=staging,
        )
        assert escaped["published"] is False
        assert hashlib.sha256(saved).hexdigest() != hashlib.sha256(b"swapped").hexdigest()
    finally:
        ledger.close()
        stop_container(name)
