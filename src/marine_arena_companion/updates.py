from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from marine_arena_companion import __version__


LOG = logging.getLogger(__name__)
_ASSET_NAME = "MarineArenaCompanion-windows-x64.zip"
_MAX_DOWNLOAD_BYTES = 1_500_000_000
_MAX_EXTRACTED_BYTES = 2_000_000_000
_VERSION_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_HASH_PATTERN = re.compile(r"^\s*([0-9a-fA-F]{64})(?:\s+[* ]?.+)?\s*$")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    title: str
    notes: str
    page_url: str
    download_url: str
    checksum_url: str | None
    digest: str | None


def validate_repository(repository: str) -> str:
    value = repository.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Enter a public GitHub repository in owner/name format.")
    return value


def default_update_repository() -> str:
    repository = os.environ.get("MARINE_ARENA_UPDATE_REPOSITORY", "").strip()
    bundled_path = Path(__file__).with_name("update_repository.txt")
    try:
        bundled_repository = bundled_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        bundled_repository = ""
    repository = repository or bundled_repository
    return validate_repository(repository) if repository else ""


def _version_key(version: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    match = _VERSION_PATTERN.fullmatch(version.strip())
    if not match:
        raise UpdateError(f"Release version is not valid semantic versioning: {version!r}")
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    prerelease = match.group(4)
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in prerelease.split(".")
    ) if prerelease else ()
    return major, minor, patch, 0 if prerelease else 1, identifiers


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return _version_key(candidate) > _version_key(current)


def _read_url(url: str, *, api: bool = False, limit: int = _MAX_DOWNLOAD_BYTES) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UpdateError("The update server returned a non-HTTPS download URL.")
    if api and parsed.hostname != "api.github.com":
        raise UpdateError("The update repository must be hosted on GitHub.")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json" if api else "application/octet-stream",
            "User-Agent": f"MarineArenaCompanion/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            final_url = urllib.parse.urlparse(response.geturl())
            valid_host = (
                final_url.hostname == "api.github.com"
                if api
                else final_url.hostname == "github.com"
                or (final_url.hostname and final_url.hostname.endswith(".githubusercontent.com"))
            )
            if final_url.scheme != "https" or not valid_host:
                raise UpdateError("The update server redirected to an untrusted or non-HTTPS location.")
            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                raise UpdateError("The update download is larger than the allowed size.")
            content = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if api and exc.code == 404:
            raise UpdateError("No stable release is published for this repository yet.") from exc
        raise UpdateError(f"Update server returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Unable to reach the update server: {exc}") from exc
    if len(content) > limit:
        raise UpdateError("The update download is larger than the allowed size.")
    return content


def latest_release(repository: str, current: str = __version__) -> Release | None:
    repository = validate_repository(repository)
    endpoint = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        payload = json.loads(_read_url(endpoint, api=True, limit=2_000_000))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned an invalid release description.") from exc
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("GitHub returned an invalid or non-stable release.")

    version = payload.get("tag_name")
    if not isinstance(version, str):
        raise UpdateError("The GitHub release does not have a version tag.")
    if not is_newer_version(version, current):
        return None

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The GitHub release has no downloadable assets.")
    app_asset = next(
        (asset for asset in assets if isinstance(asset, dict) and asset.get("name") == _ASSET_NAME),
        None,
    )
    checksum_asset = next(
        (asset for asset in assets if isinstance(asset, dict) and asset.get("name") == f"{_ASSET_NAME}.sha256"),
        None,
    )
    if not app_asset or not isinstance(app_asset.get("browser_download_url"), str):
        raise UpdateError(f"The release is missing its {_ASSET_NAME} application bundle.")
    asset_url = urllib.parse.urlparse(app_asset["browser_download_url"])
    if asset_url.scheme != "https" or asset_url.hostname != "github.com":
        raise UpdateError("The application bundle must be hosted by GitHub Releases.")
    digest = app_asset.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        digest = None
    checksum_url = checksum_asset.get("browser_download_url") if checksum_asset else None
    if isinstance(checksum_url, str):
        parsed_checksum = urllib.parse.urlparse(checksum_url)
        if parsed_checksum.scheme != "https" or parsed_checksum.hostname != "github.com":
            raise UpdateError("The release checksum must be hosted by GitHub Releases.")
    if not digest and not isinstance(checksum_url, str):
        raise UpdateError("The release is missing a SHA-256 checksum for its application bundle.")

    title = payload.get("name") or f"Version {version}"
    notes = payload.get("body") or "This release does not include release notes."
    page_url = payload.get("html_url")
    if (
        not isinstance(page_url, str)
        or urllib.parse.urlparse(page_url).scheme != "https"
        or urllib.parse.urlparse(page_url).hostname != "github.com"
    ):
        raise UpdateError("The GitHub release page URL is invalid.")
    return Release(
        version=version.lstrip("v"),
        title=str(title),
        notes=str(notes)[:1800],
        page_url=page_url,
        download_url=app_asset["browser_download_url"],
        checksum_url=checksum_url if isinstance(checksum_url, str) else None,
        digest=digest.removeprefix("sha256:").lower() if digest else None,
    )


def _download_verified_archive(release: Release, destination: Path) -> None:
    parsed = urllib.parse.urlparse(release.download_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UpdateError("The update server returned a non-HTTPS download URL.")
    request = urllib.request.Request(
        release.download_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"MarineArenaCompanion/{__version__}",
        },
    )
    checksum = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as target:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme != "https" or not (
                final_url.hostname == "github.com"
                or (final_url.hostname and final_url.hostname.endswith(".githubusercontent.com"))
            ):
                raise UpdateError("The update download redirected to an untrusted or non-HTTPS location.")
            length = response.headers.get("Content-Length")
            if length and int(length) > _MAX_DOWNLOAD_BYTES:
                raise UpdateError("The update download is larger than the allowed size.")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_DOWNLOAD_BYTES:
                    raise UpdateError("The update download is larger than the allowed size.")
                checksum.update(chunk)
                target.write(chunk)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"Update download returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Unable to download the update: {exc}") from exc

    if release.digest:
        expected = release.digest
    elif release.checksum_url:
        try:
            text = _read_url(release.checksum_url, limit=4096).decode("ascii", errors="replace")
        except UnicodeDecodeError as exc:
            raise UpdateError("The release checksum file is invalid.") from exc
        match = _HASH_PATTERN.fullmatch(text)
        if not match:
            raise UpdateError("The release checksum file is invalid.")
        expected = match.group(1).lower()
    else:
        raise UpdateError("The release has no SHA-256 checksum.")
    if checksum.hexdigest() != expected:
        raise UpdateError("The downloaded update failed its SHA-256 integrity check.")


def stage_update(release: Release, install_directory: Path) -> Path:
    install_directory = install_directory.resolve()
    if not install_directory.is_dir():
        raise UpdateError("The application installation directory is unavailable.")
    stage_root = install_directory.parent / f".marine-companion-update-{uuid.uuid4().hex}"
    archive_path = stage_root / _ASSET_NAME
    try:
        stage_root.mkdir()
        _download_verified_archive(release, archive_path)
        extracted = stage_root / "app"
        extracted.mkdir()
        total_size = 0
        with zipfile.ZipFile(archive_path) as bundle:
            for entry in bundle.infolist():
                if "\\" in entry.filename or "\x00" in entry.filename:
                    raise UpdateError("The update archive contains an unsafe file path.")
                member = PurePosixPath(entry.filename)
                if (
                    member.is_absolute()
                    or any(part in {"", ".", ".."} or ":" in part for part in member.parts)
                ):
                    raise UpdateError("The update archive contains an unsafe file path.")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UpdateError("The update archive contains an unsupported symbolic link.")
                total_size += entry.file_size
                if total_size > _MAX_EXTRACTED_BYTES:
                    raise UpdateError("The extracted update exceeds the allowed size.")
                target = extracted.joinpath(*member.parts)
                if not target.resolve().is_relative_to(extracted.resolve()):
                    raise UpdateError("The update archive contains an unsafe file path.")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        if (extracted / "MarineArenaCompanion" / "MarineArenaCompanion.exe").is_file():
            return extracted / "MarineArenaCompanion"
        if (extracted / "MarineArenaCompanion.exe").is_file():
            return extracted
        raise UpdateError("The downloaded bundle does not contain the Windows application.")
    except (OSError, ValueError, NotImplementedError, zipfile.BadZipFile, RuntimeError) as exc:
        shutil.rmtree(stage_root, ignore_errors=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(f"Unable to unpack the application update: {exc}") from exc


def remove_staged_update(staged_directory: Path) -> None:
    stage_root = staged_directory.parent
    if stage_root.name == "app":
        stage_root = stage_root.parent
    shutil.rmtree(stage_root, ignore_errors=True)


def launch_installer(staged_directory: Path, install_directory: Path, data_directory: Path) -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateError("Installing updates is supported only by the packaged Windows application.")
    install_directory = install_directory.resolve()
    staged_directory = staged_directory.resolve()
    data_directory = data_directory.resolve()
    executable = Path(sys.executable).name
    backup_directory = install_directory.with_name(
        f"{install_directory.name}.backup-{uuid.uuid4().hex}"
    )
    stage_root = staged_directory.parent
    if stage_root.name == "app":
        stage_root = stage_root.parent
    script = data_directory / "updates" / f"install-{uuid.uuid4().hex}.ps1"
    log_path = data_directory / "logs" / "updater.log"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(_INSTALLER_SCRIPT, encoding="utf-8")
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script), "-ParentPid", str(os.getpid()),
                "-InstallDirectory", str(install_directory),
                "-StagedDirectory", str(staged_directory),
                "-StageRootDirectory", str(stage_root),
                "-BackupDirectory", str(backup_directory),
                "-ExecutableName", executable, "-LogPath", str(log_path),
            ],
            close_fds=True,
            creationflags=creation_flags,
            cwd=str(data_directory),
        )
    except (OSError, ValueError) as exc:
        raise UpdateError(f"Unable to start the update installer: {exc}") from exc


_INSTALLER_SCRIPT = r"""
param(
    [int]$ParentPid,
    [string]$InstallDirectory,
    [string]$StagedDirectory,
    [string]$StageRootDirectory,
    [string]$BackupDirectory,
    [string]$ExecutableName,
    [string]$LogPath
)
$ErrorActionPreference = 'Stop'
function Write-UpdateLog([string]$Message) {
    Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) $Message"
}
try {
    try { Wait-Process -Id $ParentPid -Timeout 180 -ErrorAction Stop } catch {
        if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) { throw }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $StagedDirectory $ExecutableName) -PathType Leaf)) {
        throw 'The staged application executable is missing.'
    }
    Move-Item -LiteralPath $InstallDirectory -Destination $BackupDirectory
    try {
        Move-Item -LiteralPath $StagedDirectory -Destination $InstallDirectory
        Start-Process -FilePath (Join-Path $InstallDirectory $ExecutableName) -WorkingDirectory $InstallDirectory
    } catch {
        if (Test-Path -LiteralPath $InstallDirectory) {
            Remove-Item -LiteralPath $InstallDirectory -Recurse -Force
        }
        Move-Item -LiteralPath $BackupDirectory -Destination $InstallDirectory
        throw
    }
    Write-UpdateLog "Installed $ExecutableName successfully."
    try { Remove-Item -LiteralPath $BackupDirectory -Recurse -Force -ErrorAction Stop } catch {
        Write-UpdateLog "Update succeeded, but the backup could not be removed: $($_.Exception.Message)"
    }
    try { Remove-Item -LiteralPath $StageRootDirectory -Recurse -Force -ErrorAction Stop } catch {
        Write-UpdateLog "Update succeeded, but temporary files could not be removed: $($_.Exception.Message)"
    }
} catch {
    Remove-Item -LiteralPath $StageRootDirectory -Recurse -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Marine Arena Companion could not finish updating. Your previous version has been restored.`n`n$($_.Exception.Message)",
        'Update failed', 'OK', 'Error'
    ) | Out-Null
}
"""
