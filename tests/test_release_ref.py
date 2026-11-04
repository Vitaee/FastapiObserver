"""Release identity is checked against real Git tags, not version text alone."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_ref.py"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def release_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Release Test")
    version = tmp_path / "src/fastapiobserver/_version.py"
    version.parent.mkdir(parents=True)
    version.write_text('__version__ = "2.2.0"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "release")
    git(tmp_path, "tag", "v2.2.0")
    return tmp_path


def resolve(repo, tag):
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--tag", tag],
                          capture_output=True, text=True, timeout=5)


def test_release_uses_the_tagged_commit(release_repo):
    result = resolve(release_repo, "2.2.0")
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert metadata["commit"] == git(release_repo, "rev-parse", "refs/tags/v2.2.0^{commit}")
    assert metadata["tag"] == "v2.2.0"


def test_same_version_on_another_commit_cannot_be_released(release_repo):
    (release_repo / "unreleased.py").write_text("changed = True\n")
    git(release_repo, "add", ".")
    git(release_repo, "commit", "-qm", "different code, same version")
    result = resolve(release_repo, "v2.2.0")
    assert result.returncode != 0
    assert "commit" in result.stderr.lower()


@pytest.mark.parametrize("tag", ["v2.2.1", "v2.2.0;exit 0", "missing"])
def test_wrong_version_or_invalid_release_tag_is_rejected(release_repo, tag):
    assert resolve(release_repo, tag).returncode != 0


def test_release_retry_rejects_existing_files_with_different_bytes(tmp_path):
    from scripts.release_ref import verify_existing_files

    (tmp_path / "package.whl").write_bytes(b"validated wheel")
    with pytest.raises(ValueError, match="different bytes"):
        verify_existing_files(tmp_path, {"package.whl": "not-the-validated-hash"})


def test_release_retry_accepts_identical_existing_files(tmp_path):
    from hashlib import sha256
    from scripts.release_ref import verify_existing_files

    (tmp_path / "package.whl").write_bytes(b"validated wheel")
    assert verify_existing_files(
        tmp_path, {"package.whl": sha256(b"validated wheel").hexdigest()},
    ) == []


def test_partial_release_uploads_only_files_not_already_verified(tmp_path):
    from hashlib import sha256
    from scripts.release_ref import verify_existing_files

    (tmp_path / "package.whl").write_bytes(b"validated wheel")
    missing = tmp_path / "package.tar.gz"
    missing.write_bytes(b"validated source")
    assert verify_existing_files(
        tmp_path, {"package.whl": sha256(b"validated wheel").hexdigest()},
    ) == [missing]


def test_release_command_stages_only_the_missing_validated_bytes(release_repo, monkeypatch, capsys):
    from hashlib import sha256
    import scripts.release_ref as release

    packages = release_repo / "packages"
    packages.mkdir()
    (packages / "package.whl").write_bytes(b"validated wheel")
    (packages / "package.tar.gz").write_bytes(b"validated source")
    output = release_repo / "upload"
    monkeypatch.setattr(release, "published_hashes", lambda version: {
        "package.whl": sha256(b"validated wheel").hexdigest(),
    })
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--repo", str(release_repo),
                                    "--tag", "v2.2.0", "--distributions", str(packages),
                                    "--upload-directory", str(output)])
    assert release.main() == 0
    assert json.loads(capsys.readouterr().out)["upload_required"] == "true"
    assert sorted(path.name for path in output.iterdir()) == ["package.tar.gz"]
    assert (output / "package.tar.gz").read_bytes() == b"validated source"
