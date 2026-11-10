import subprocess
import sys


def test_runtime_import_does_not_load_research_package():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import epochgate_runtime; import sys; raise SystemExit(0 if 'epochgate' not in sys.modules else 1)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
