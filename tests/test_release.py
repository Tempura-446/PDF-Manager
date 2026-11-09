"""packaging/release.py: the release zip, its notes, and publishing."""
import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def release():
    # Loaded by path: the folder is called "packaging", like the library.
    spec = importlib.util.spec_from_file_location("zpdf_release", ROOT / "packaging" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

## 9.9.9

- Thing one.
- Thing two.

## 9.9.8

- Older.
"""


def test_release_notes_come_from_the_changelog(release):
    assert release.changelog_section(CHANGELOG, "9.9.9") == "- Thing one.\n- Thing two."
    assert release.changelog_section(CHANGELOG, "9.9.8") == "- Older."
    with pytest.raises(release.ReleaseError, match="CHANGELOG.md has no section for 1.0.0"):
        release.changelog_section(CHANGELOG, "1.0.0")


def test_the_zip_holds_the_installer_and_nothing_else(release, tmp_path):
    zip_path, sha_path = release.make_zip(ROOT, tmp_path, "9.9.9", "NOTICES TEXT")
    assert zip_path.name == "ZPDF-9.9.9-windows.zip" and sha_path.name == "ZPDF-9.9.9-windows.zip.sha256"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert zf.read("ZPDF-9.9.9/THIRD-PARTY-NOTICES.txt").decode() == "NOTICES TEXT"
    for needed in ("Install ZPDF.cmd", "packaging/windows/install.ps1", "pyproject.toml", "LICENSE",
                   "README.md", "pdfmanager/__init__.py", "pdfmanager/gui.html",
                   "pdfmanager/static/pdfjs/build/pdf.min.mjs"):
        assert f"ZPDF-9.9.9/{needed}" in names, needed
    assert not any(n.startswith(("ZPDF-9.9.9/tests/", "ZPDF-9.9.9/docs/", "ZPDF-9.9.9/.github/")) for n in names)
    assert all(n.startswith("ZPDF-9.9.9/") for n in names)
    digest, name = sha_path.read_text().split()
    assert name == zip_path.name and digest == hashlib.sha256(zip_path.read_bytes()).hexdigest()


def test_the_zip_is_the_same_every_time(release, tmp_path):
    a, _ = release.make_zip(ROOT, tmp_path / "a", "9.9.9", "N")
    b, _ = release.make_zip(ROOT, tmp_path / "b", "9.9.9", "N")
    assert a.read_bytes() == b.read_bytes()


class Runner:
    """Stands in for git and gh."""

    def __init__(self, dirty="", released=False):
        self.dirty, self.released, self.commands = dirty, released, []

    def __call__(self, cmd, cwd=None, check=True):
        self.commands.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return self.dirty
        if cmd[:3] == ["gh", "release", "view"]:
            if self.released:
                return "v9.9.9"
            raise release_error("release not found")
        if cmd[:2] == ["git", "tag"] and "-l" in cmd:
            return ""
        return ""


def release_error(text):
    return RuntimeError(text)


def test_publishing_uploads_to_the_releases_repo_and_tags(release, tmp_path, monkeypatch):
    runner = Runner()
    monkeypatch.setattr(release, "run", runner)
    monkeypatch.setattr(release, "third_party_notices", lambda root: "N")
    release.publish(ROOT, "9.9.9", CHANGELOG, out_dir=tmp_path, checks=False)
    create = next(c for c in runner.commands if c[:3] == ["gh", "release", "create"])
    assert create[3] == "v9.9.9" and "--repo" in create and create[create.index("--repo") + 1] == release.RELEASES
    assert any(arg.endswith("ZPDF-9.9.9-windows.zip") for arg in create)
    assert any(arg.endswith("ZPDF-9.9.9-windows.zip.sha256") for arg in create)
    notes = Path(create[create.index("--notes-file") + 1]).read_text(encoding="utf-8")
    assert "- Thing one." in notes and "Install ZPDF.cmd" in notes
    assert ["git", "tag", "-a", "v9.9.9", "-m", "ZPDF 9.9.9"] in runner.commands
    assert ["git", "push", "origin", "v9.9.9"] in runner.commands


def test_publishing_refuses_a_dirty_tree_or_a_released_version(release, tmp_path, monkeypatch):
    monkeypatch.setattr(release, "third_party_notices", lambda root: "N")
    monkeypatch.setattr(release, "run", Runner(dirty=" M pdfmanager/core.py"))
    with pytest.raises(release.ReleaseError, match="uncommitted changes"):
        release.publish(ROOT, "9.9.9", CHANGELOG, out_dir=tmp_path, checks=False)
    monkeypatch.setattr(release, "run", Runner(released=True))
    with pytest.raises(release.ReleaseError, match="already published"):
        release.publish(ROOT, "9.9.9", CHANGELOG, out_dir=tmp_path, checks=False)
    runner = Runner()
    monkeypatch.setattr(release, "run", runner)
    with pytest.raises(release.ReleaseError, match="no section for 9.9.7"):
        release.publish(ROOT, "9.9.7", CHANGELOG, out_dir=tmp_path, checks=False)
    assert not any(c[:3] == ["gh", "release", "create"] for c in runner.commands)


def test_a_dry_run_makes_the_files_but_publishes_nothing(release, tmp_path, monkeypatch):
    runner = Runner()
    monkeypatch.setattr(release, "run", runner)
    monkeypatch.setattr(release, "third_party_notices", lambda root: "N")
    files = release.publish(ROOT, "9.9.9", CHANGELOG, out_dir=tmp_path, checks=False, dry_run=True)
    assert all(Path(f).exists() for f in files)
    assert not any(c[0] == "gh" and c[1] == "release" and c[2] == "create" for c in runner.commands)
    assert not any(c[:2] == ["git", "tag"] for c in runner.commands)


def test_the_checks_stop_at_the_first_failure(release, tmp_path, monkeypatch):
    calls = []

    def fake_step(name, cmd, cwd):
        calls.append(name)
        return name != "tests"

    monkeypatch.setattr(release, "_step", fake_step)
    assert release.check(ROOT) is False and calls == ["tests"]
    calls.clear()
    monkeypatch.setattr(release, "_step", lambda name, cmd, cwd: calls.append(name) or True)
    monkeypatch.setattr(release, "_self_test", lambda root: (calls.append("self-test") or True))
    assert release.check(ROOT) is True and calls == ["tests", "build", "self-test"]
