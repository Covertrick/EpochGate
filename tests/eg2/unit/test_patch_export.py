from pathlib import Path

from epochgate.eg2_patch import classify_trees, load_policy, with_apply_result


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_bytecode_and_harness_files_do_not_become_a_patch(tmp_path: Path):
    pristine = tmp_path / "pristine"
    work = tmp_path / "work"
    _write(pristine, "astropy/modeling/separable.py", "def value():\n    return 1\n")
    _write(work, "astropy/modeling/separable.py", "def value():\n    return 1\n")
    _write(work, "astropy/__pycache__/separable.cpython-310.pyc", "not-a-diff")
    _write(work, "astropy/_version.py", 'version = "0.0.0"\n')
    result = classify_trees(pristine, work, load_policy())
    assert result["kind"] == "EMPTY_PATCH"
    assert result["files"] == []


def test_source_edit_is_a_valid_patch_and_a_failed_apply_is_invalid(tmp_path: Path):
    pristine = tmp_path / "pristine"
    work = tmp_path / "work"
    _write(pristine, "astropy/modeling/separable.py", "def value():\n    return 1\n")
    _write(work, "astropy/modeling/separable.py", "def value():\n    return 2\n")
    _write(work, "astropy/modeling/tests/test_new.py", "def test_new():\n    assert True\n")
    result = classify_trees(pristine, work, load_policy())
    assert result["kind"] == "VALID_PATCH"
    assert "astropy/modeling/separable.py" in result["files"]
    assert "astropy/modeling/tests/test_new.py" in result["files"]
    assert "diff --git" in result["diff"]
    assert "\0" not in result["diff"]
    invalid = with_apply_result(result, False, "corrupt")
    assert invalid["kind"] == "INVALID_PATCH"


def test_binary_source_is_an_export_error(tmp_path: Path):
    pristine = tmp_path / "pristine"
    work = tmp_path / "work"
    _write(pristine, "astropy/modeling/separable.py", "x")
    target = work / "astropy/modeling/separable.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"a\0b")
    result = classify_trees(pristine, work, load_policy())
    assert result["kind"] == "EXPORT_ERROR"
