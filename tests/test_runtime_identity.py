"""Runtime pins fail closed and respond to pipeline source changes."""

import json
import shutil
from importlib import metadata
from pathlib import Path

import pytest

from headroom.provenance import runtime


@pytest.fixture
def package_root(tmp_path):
    root = tmp_path / "headroom"
    root.mkdir()
    (root / "__init__.py").write_text('__version__ = "0.0.0"\n')
    (root / "pipeline.py").write_text("def run_pipeline(): pass\n")
    for directory in runtime._SOURCE_DIRS:
        source_dir = root / directory
        source_dir.mkdir()
        (source_dir / "__init__.py").write_text("")
    (root / "model" / "score.py").write_text("SCORE = 1\n")
    return root


def test_source_identity_is_deterministic_and_location_independent(package_root, tmp_path):
    copied = tmp_path / "another-location" / "headroom"
    shutil.copytree(package_root, copied)
    digest = runtime._source_sha256(package_root)
    assert len(digest) == 64
    assert runtime._source_sha256(package_root) == digest
    assert runtime._source_sha256(copied) == digest


@pytest.mark.parametrize("change", ["content", "rename", "add", "remove"])
def test_pipeline_source_changes_alter_identity(package_root, change):
    original = runtime._source_sha256(package_root)
    score = package_root / "model" / "score.py"
    if change == "content":
        score.write_text("SCORE = 2\n")
    elif change == "rename":
        score.rename(score.with_name("renamed_score.py"))
    elif change == "add":
        score.with_name("new_score.py").write_bytes(score.read_bytes())
    else:
        score.unlink()
    assert runtime._source_sha256(package_root) != original


def test_caches_ui_and_other_unrelated_files_do_not_change_identity(package_root):
    original = runtime._source_sha256(package_root)
    excluded = (
        "app/streamlit_app.py", "mcp/server.py", "cli.py", "export.py",
        "model/__pycache__/score.py", "model/__pycache__/score.cpython-311.pyc",
        "model/notes.txt", ".git/config",
    )
    for relative in excluded:
        path = package_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unrelated content")
    assert runtime._source_sha256(package_root) == original


@pytest.mark.parametrize("relative", ["pipeline.py", "__init__.py", "model/__init__.py"])
def test_missing_required_source_fails_clearly(package_root, relative):
    (package_root / relative).unlink()
    with pytest.raises(RuntimeError, match="Required pipeline source is missing"):
        runtime._source_sha256(package_root)


def test_unreadable_source_fails_clearly(monkeypatch, package_root):
    original_read = Path.read_bytes

    def read_bytes(path):
        if path.name == "score.py":
            raise OSError("unreadable")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(RuntimeError, match="Cannot read pipeline source: model/score.py"):
        runtime._source_sha256(package_root)


def test_runtime_identity_has_populated_serializable_version_fields():
    identity = runtime.runtime_identity()
    assert identity["schema_version"] == 1
    assert identity["python"]["version"]
    assert identity["python"]["implementation"]
    assert identity["platform"]["system"]
    assert identity["platform"]["machine"]
    assert identity["dependencies"] == {
        name: metadata.version(name) for name in ("numpy", "pydantic", "PyYAML")
    }
    assert len(identity["source_sha256"]) == 64
    assert json.loads(json.dumps(identity)) == identity
    assert runtime.runtime_identity() == identity


@pytest.mark.parametrize("missing", ["numpy", "pydantic", "PyYAML"])
def test_missing_dependency_metadata_fails_clearly(monkeypatch, missing):
    def version(name):
        if name == missing:
            raise metadata.PackageNotFoundError(name)
        return "1.0"

    monkeypatch.setattr(runtime.metadata, "version", version)
    with pytest.raises(RuntimeError, match=f"dependency metadata is missing: {missing}"):
        runtime.runtime_identity()


def test_empty_dependency_version_fails_clearly(monkeypatch):
    monkeypatch.setattr(runtime.metadata, "version", lambda name: "")
    with pytest.raises(RuntimeError, match="dependency version is missing: numpy"):
        runtime.runtime_identity()
