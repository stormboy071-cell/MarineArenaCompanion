from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

import marine_arena_companion.updates as updates
from marine_arena_companion.updates import Release, UpdateError


def test_repository_and_semantic_version_validation() -> None:
    assert updates.validate_repository("community/marine-companion") == "community/marine-companion"
    with pytest.raises(ValueError):
        updates.validate_repository("https://github.com/community/marine-companion")
    assert updates.is_newer_version("v0.1.1", "0.1.0")
    assert updates.is_newer_version("0.1.0", "0.1.0-rc.2")
    assert not updates.is_newer_version("0.1.0-rc.2", "0.1.0")
    assert not updates.is_newer_version("0.1.0", "0.1.0")
    assert updates.is_newer_version("0.1.0-rc.11", "0.1.0-rc.2")


def test_default_update_repository_reads_bundled_or_environment_source(tmp_path: Path, monkeypatch) -> None:
    bundled_path = tmp_path / "updates.py"
    repository_path = bundled_path.with_name("update_repository.txt")
    monkeypatch.setattr(updates, "__file__", str(bundled_path))
    monkeypatch.delenv("MARINE_ARENA_UPDATE_REPOSITORY", raising=False)
    repository_path.write_text("community/marine-companion", encoding="utf-8")
    assert updates.default_update_repository() == "community/marine-companion"

    monkeypatch.setenv("MARINE_ARENA_UPDATE_REPOSITORY", "community/companion-fork")
    assert updates.default_update_repository() == "community/companion-fork"


def test_latest_release_requires_bundle_and_checksum(monkeypatch) -> None:
    payload = {
        "tag_name": "v0.2.0",
        "name": "Community update",
        "body": "Improvements",
        "html_url": "https://github.com/community/marine-companion/releases/tag/v0.2.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "MarineArenaCompanion-windows-x64.zip",
                "browser_download_url": "https://github.com/community/marine-companion/releases/download/v0.2.0/app.zip",
            },
        ],
    }
    monkeypatch.setattr(updates, "_read_url", lambda *args, **kwargs: json.dumps(payload).encode())
    with pytest.raises(UpdateError, match="checksum"):
        updates.latest_release("community/marine-companion", current="0.1.0")

    payload["assets"].append({
        "name": "MarineArenaCompanion-windows-x64.zip.sha256",
        "browser_download_url": "https://github.com/community/marine-companion/releases/download/v0.2.0/app.zip.sha256",
    })
    release = updates.latest_release("community/marine-companion", current="0.1.0")
    assert release is not None
    assert release.version == "0.2.0"
    assert release.checksum_url.endswith(".zip.sha256")


class _DownloadResponse(io.BytesIO):
    headers = {"Content-Length": "1"}

    def __init__(self, content: bytes):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}

    def geturl(self) -> str:
        return "https://release-assets.githubusercontent.com/app.zip"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_stage_update_checks_checksum_and_extracts_windows_bundle(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "installed" / "MarineArenaCompanion"
    install.mkdir(parents=True)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("MarineArenaCompanion.exe", b"new executable")
        bundle.writestr("_internal/runtime.dll", b"runtime")
    content = archive.getvalue()
    release = Release(
        version="0.2.0", title="Update", notes="", page_url="https://github.com/example/repo",
        download_url="https://github.com/example/repo/app.zip", checksum_url=None,
        digest=hashlib.sha256(content).hexdigest(),
    )
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_args, **_kwargs: _DownloadResponse(content))

    staged = updates.stage_update(release, install)
    assert (staged / "MarineArenaCompanion.exe").read_bytes() == b"new executable"
    assert (staged / "_internal" / "runtime.dll").read_bytes() == b"runtime"


def test_stage_update_rejects_bad_checksum_and_zip_traversal(tmp_path: Path, monkeypatch) -> None:
    install = tmp_path / "installed" / "MarineArenaCompanion"
    install.mkdir(parents=True)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", b"unsafe")
        bundle.writestr("MarineArenaCompanion.exe", b"new executable")
    content = archive.getvalue()
    release = Release(
        version="0.2.0", title="Update", notes="", page_url="https://github.com/example/repo",
        download_url="https://github.com/example/repo/app.zip", checksum_url=None, digest="0" * 64,
    )
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_args, **_kwargs: _DownloadResponse(content))
    with pytest.raises(UpdateError, match="integrity"):
        updates.stage_update(release, install)
    assert not (tmp_path / "outside.txt").exists()

    release = Release(
        version="0.2.0", title="Update", notes="", page_url="https://github.com/example/repo",
        download_url="https://github.com/example/repo/app.zip", checksum_url=None,
        digest=hashlib.sha256(content).hexdigest(),
    )
    with pytest.raises(UpdateError, match="unsafe file path"):
        updates.stage_update(release, install)
    assert not (tmp_path / "outside.txt").exists()
