"""Check, package and publish a ZPDF release for friends to download.

    python packaging/release.py --check      run the checks only (the tests with the performance
                                             limits, the build, the self-test)
    python packaging/release.py --dry-run    the checks, then the release files, without publishing
    python packaging/release.py              the checks, the files, then publish

The release is one zip, ZPDF-<version>-windows.zip: the Windows installer
("Install ZPDF.cmd"), ZPDF's source it installs from, the LICENSE and the
third-party notices, with a SHA-256 checksum file beside it. Publishing
uploads both to the public releases repository (RELEASES) with GitHub's
"gh" program, as a release named after the version with its CHANGELOG.md
section as notes, and tags the development repository. The app's Update
button follows those releases (see pdfmanager/update.py).

Run it on Windows (the build and the self-test make the Windows app), from
a clean checkout of the commit to release, with GitHub's "gh" installed
("winget install --id GitHub.cli") and signed in ("gh auth login"), and
git's user.name and user.email set (the version tag carries them). A
checkout with core.autocrlf (Windows' usual git setting) gives the text
files CRLF line endings, so its zip's checksum differs from one made on
Linux from the same commit. It refuses a tree with uncommitted changes, a version CHANGELOG.md
doesn't describe, and a version that is already published.
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = "Tempura-446/ZPDF-releases"
# What friends get: the installer and what it installs from. Tests, docs and
# the tools for building and publishing stay behind.
INCLUDE = ("pdfmanager/", "packaging/windows/", "Install ZPDF.cmd", "pyproject.toml", "requirements.txt",
           "README.md", "LICENSE", "CHANGELOG.md")
ZIP_DATE = (2026, 1, 1, 0, 0, 0)  # every entry, so the same files make the same zip

INSTALL_NOTE = """
## Installing

Download `ZPDF-{version}-windows.zip`, unzip it, and double-click
`Install ZPDF.cmd`. Already have ZPDF? Use the Update button in the app.
"""


class ReleaseError(Exception):
    pass


def run(cmd, cwd=None, check=True):
    """Run a program; its output, or ReleaseError with what it said (or
    that it isn't installed)."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        raise ReleaseError(f"{cmd[0]} isn't installed or can't be started ({exc})") from None
    if check and proc.returncode:
        said = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise ReleaseError(f"{' '.join(cmd[:3])} failed: " + " / ".join(said))
    return proc.stdout


def read_version(root=ROOT):
    text = (root / "pdfmanager" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text).group(1)


def changelog_section(text, version):
    """The notes for ``version``: its "## <version>" section of CHANGELOG.md."""
    found = re.search(rf"^## {re.escape(version)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not found or not found.group(1).strip():
        raise ReleaseError(f"CHANGELOG.md has no section for {version}")
    return found.group(1).strip()


def _tracked_files(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    names = [n for n in out.decode("utf-8").split("\0") if n]
    return sorted(n for n in names if n.startswith(INCLUDE) and (root / n).is_file())


def make_zip(root, out_dir, version, notices):
    """ZPDF-<version>-windows.zip (everything under ZPDF-<version>/) and its
    .sha256 file in ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"ZPDF-{version}-windows.zip"
    top = f"ZPDF-{version}/"
    entries = [(name, (Path(root) / name).read_bytes()) for name in _tracked_files(root)]
    entries.append(("THIRD-PARTY-NOTICES.txt", notices.encode("utf-8")))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(top + name, ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha_path = out_dir / (zip_path.name + ".sha256")
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="ascii", newline="\n")
    return zip_path, sha_path


def third_party_notices(root):
    sys.path.insert(0, str(root))
    try:
        from pdfmanager import notices
        return notices.third_party_notices()
    finally:
        sys.path.remove(str(root))


def _step(name, cmd, cwd):
    print(f"\n> {name}: {' '.join(cmd)}", flush=True)
    ok = subprocess.run(cmd, cwd=cwd).returncode == 0
    print(f"  {name}: {'passed' if ok else 'FAILED'}", flush=True)
    return ok


def _self_test(root):
    exe = root / "dist" / "ZPDF" / ("ZPDF.exe" if sys.platform == "win32" else "ZPDF")
    with tempfile.TemporaryDirectory() as tmp:
        result = Path(tmp) / "result.txt"
        print(f"\n> self-test: {exe} --self-test", flush=True)
        subprocess.run([str(exe), "--self-test", str(result)], cwd=root)
        said = result.read_text(encoding="utf-8", errors="replace").strip() if result.exists() else "no result"
    ok = said == "ok"
    print(f"  self-test: {'passed' if ok else 'FAILED: ' + said}", flush=True)
    return ok


def check(root=ROOT):
    """The checks a release must pass, in order, stopping at the first
    failure: the full test suite with the performance limits (--perf, a few
    minutes more), the app build, the built app's self-test."""
    python = sys.executable
    return (_step("tests", [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--perf"], root)
            and _step("build", [python, str(root / "packaging" / "build_app.py")], root)
            and _self_test(root))


def publish(root, version, changelog, out_dir, checks=True, dry_run=False):
    """Make the release files and, unless ``dry_run``, publish them. Returns
    the files: the zip, its checksum and the notes."""
    notes = changelog_section(changelog, version) + "\n" + INSTALL_NOTE.format(version=version)
    tag = f"v{version}"
    if not dry_run:
        # Found out now, not after the checks: publishing needs gh, signed in.
        if not shutil.which("gh"):
            raise ReleaseError("gh isn't installed; install GitHub CLI (winget install --id GitHub.cli) "
                               "and sign in with gh auth login")
        try:
            run(["gh", "auth", "status"], cwd=root)
        except Exception:
            raise ReleaseError("gh isn't signed in to GitHub; run gh auth login") from None
        # The version tag is signed with git's name and email.
        if not all(run(["git", "config", key], cwd=root, check=False).strip() for key in ("user.name", "user.email")):
            raise ReleaseError('git has no name or email to tag the version with; run once in the repository: '
                               'git config user.name "Your Name" and git config user.email you@example.com')
        if run(["git", "status", "--porcelain"], cwd=root).strip():
            raise ReleaseError("the checkout has uncommitted changes; release a committed version")
        if run(["git", "tag", "-l", tag], cwd=root).strip():
            raise ReleaseError(f"version {version} is already published (tag {tag} exists)")
        try:
            run(["gh", "release", "view", tag, "--repo", RELEASES], cwd=root)
        except Exception:
            pass  # not published yet
        else:
            raise ReleaseError(f"version {version} is already published on {RELEASES}")
    if checks and not check(root):
        raise ReleaseError("the checks failed (see above); nothing was published")
    zip_path, sha_path = make_zip(root, out_dir, version, third_party_notices(root))
    notes_path = Path(out_dir) / f"ZPDF-{version}-notes.md"
    notes_path.write_text(notes, encoding="utf-8", newline="\n")
    files = [zip_path, sha_path, notes_path]
    if dry_run:
        return files
    # Tag first: it is local and can't half-succeed. The upload is the step
    # that makes the release public; only the push comes after it.
    run(["git", "tag", "-a", tag, "-m", f"ZPDF {version}"], cwd=root)
    try:
        run(["gh", "release", "create", tag, str(zip_path), str(sha_path), "--repo", RELEASES,
             "--title", f"ZPDF {version}", "--notes-file", str(notes_path)], cwd=root)
    except Exception as exc:
        run(["git", "tag", "-d", tag], cwd=root, check=False)
        raise ReleaseError(str(exc)) from None
    try:
        run(["git", "push", "origin", tag], cwd=root)
    except Exception as exc:
        raise ReleaseError(f"ZPDF {version} is published on {RELEASES}, but pushing its tag failed ({exc}); "
                           f"push it with: git push origin {tag}") from None
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="run the checks only")
    parser.add_argument("--dry-run", action="store_true", help="make the release files but don't publish")
    parser.add_argument("--skip-checks", action="store_true", help="don't run the checks first")
    parser.add_argument("--out", default=str(ROOT / "dist" / "release"), help="where to put the files")
    args = parser.parse_args(argv)
    try:
        if args.check:
            ok = check(ROOT)
            print("\nAll checks passed." if ok else "\nThe checks failed.")
            return 0 if ok else 1
        version = read_version(ROOT)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        files = publish(ROOT, version, changelog, args.out, checks=not args.skip_checks, dry_run=args.dry_run)
    except ReleaseError as exc:
        said = str(exc)
        print(("\n" if " is published on " in said else "\nNot released: ") + said, file=sys.stderr)
        return 1
    print("\n" + "\n".join(str(f) for f in files))
    print(f"\nMade the release files for {version}; nothing was published." if args.dry_run
          else f"\nPublished ZPDF {version}: https://github.com/{RELEASES}/releases/tag/v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
