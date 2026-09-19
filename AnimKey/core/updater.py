"""GitHub Release discovery and atomic package installation for AnimKey.

Only published, non-prerelease GitHub Releases are exposed as stable builds.
Release archives are required to include a SHA-256 digest, either through the
GitHub asset metadata or a companion ``.sha256`` asset.
"""

from __future__ import absolute_import

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field

from AnimKey.version import __version__


GITHUB_REPOSITORY = "gnzlf/animkey"
RELEASES_URL = "https://api.github.com/repos/{}/releases?per_page=100".format(
    GITHUB_REPOSITORY
)
RELEASES_PAGE_URL = "https://github.com/{}/releases".format(GITHUB_REPOSITORY)
GITHUB_API_VERSION = "2026-03-10"
GITHUB_TOKEN_ENV = "ANIMKEY_GITHUB_TOKEN"
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000
MAX_METADATA_BYTES = 10 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)
SHA256_PATTERN = re.compile(r"\b([0-9a-fA-F]{64})\b")
REQUIRED_PACKAGE_FILES = (
    "__init__.py",
    "version.py",
    os.path.join("core", "toolbar.py"),
    os.path.join("mods", "maya_compat.py"),
)


class UpdateError(RuntimeError):
    """A user-facing updater failure."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    name: str
    published_at: str
    notes: str
    html_url: str
    download_url: str = ""
    checksum_url: str = ""
    digest: str = ""
    asset_name: str = ""

    @property
    def installable(self):
        return bool(self.download_url and (self.digest or self.checksum_url))


@dataclass
class InstallResult:
    version: str
    warnings: list = field(default_factory=list)


def package_root():
    """Return the currently loaded ``AnimKey`` package directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_version(value):
    match = VERSION_PATTERN.match(str(value or "").strip())
    if not match:
        return None
    return ".".join(str(int(part)) for part in match.groups())


def version_key(value):
    normalized = normalize_version(value)
    if normalized is None:
        raise ValueError("Invalid AnimKey version: {}".format(value))
    return tuple(int(part) for part in normalized.split("."))


def compare_versions(left, right):
    left_key = version_key(left)
    right_key = version_key(right)
    return (left_key > right_key) - (left_key < right_key)


def _request(url, token=None, accept="application/vnd.github+json"):
    headers = {
        "Accept": accept,
        "User-Agent": "AnimKey-Updater/{}".format(__version__),
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    token = token if token is not None else os.environ.get(GITHUB_TOKEN_ENV, "")
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return urllib.request.Request(url, headers=headers)


def _read_url(
    url,
    timeout=20,
    token=None,
    opener=None,
    accept="application/vnd.github+json",
    max_bytes=None,
):
    open_url = opener or urllib.request.urlopen
    request = _request(url, token=token, accept=accept)
    try:
        response = open_url(request, timeout=timeout)
        try:
            data = response.read(max_bytes + 1) if max_bytes else response.read()
            if max_bytes and len(data) > max_bytes:
                raise UpdateError("GitHub returned more data than the updater safety limit.")
            return data
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "The AnimKey releases are not publicly accessible on GitHub. "
                "Make the release repository public or set {} before starting Maya.".format(
                    GITHUB_TOKEN_ENV
                )
            )
        if exc.code == 403:
            raise UpdateError(
                "GitHub refused the update check (rate limit or repository access)."
            )
        raise UpdateError("GitHub returned HTTP {}.".format(exc.code))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError("Could not connect to GitHub: {}".format(reason))
    except OSError as exc:
        raise UpdateError("Could not read the update: {}".format(exc))


def _asset_for_version(assets, version):
    accepted = {
        "animkey-{}.zip".format(version).lower(),
        "animkey-v{}.zip".format(version).lower(),
    }
    for asset in assets or []:
        if str(asset.get("name", "")).lower() in accepted:
            return asset
    return None


def _checksum_asset(assets, archive_name):
    accepted = {
        "{}.sha256".format(archive_name).lower(),
        "sha256sums.txt",
    }
    for asset in assets or []:
        if str(asset.get("name", "")).lower() in accepted:
            return asset
    return None


def parse_releases(payload):
    """Convert GitHub API data into sorted stable release records."""
    if not isinstance(payload, list):
        raise UpdateError("GitHub returned an unexpected releases response.")

    releases = []
    seen_versions = set()
    for raw in payload:
        if not isinstance(raw, dict) or raw.get("draft") or raw.get("prerelease"):
            continue
        version = normalize_version(raw.get("tag_name"))
        if version is None or version in seen_versions:
            continue
        seen_versions.add(version)

        assets = raw.get("assets") or []
        archive = _asset_for_version(assets, version)
        checksum = _checksum_asset(assets, archive.get("name", "")) if archive else None
        digest = str((archive or {}).get("digest") or "")
        if digest.lower().startswith("sha256:"):
            digest = digest.split(":", 1)[1]
        if not SHA256_PATTERN.fullmatch(digest):
            digest = ""

        releases.append(
            ReleaseInfo(
                version=version,
                tag_name=str(raw.get("tag_name") or "v{}".format(version)),
                name=str(raw.get("name") or "AnimKey {}".format(version)),
                published_at=str(raw.get("published_at") or ""),
                notes=str(raw.get("body") or ""),
                html_url=str(raw.get("html_url") or ""),
                download_url=str(
                    (archive or {}).get("url")
                    or (archive or {}).get("browser_download_url")
                    or ""
                ),
                checksum_url=str(
                    (checksum or {}).get("url")
                    or (checksum or {}).get("browser_download_url")
                    or ""
                ),
                digest=digest.lower(),
                asset_name=str((archive or {}).get("name") or ""),
            )
        )

    releases.sort(key=lambda release: version_key(release.version), reverse=True)
    return releases


def fetch_releases(timeout=20, token=None, opener=None, api_url=RELEASES_URL):
    """Fetch every stable GitHub Release available to the current user."""
    raw = _read_url(
        api_url,
        timeout=timeout,
        token=token,
        opener=opener,
        max_bytes=MAX_METADATA_BYTES,
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError("GitHub returned invalid release data: {}".format(exc))
    return parse_releases(payload)


def _is_within(path, parent):
    try:
        resolved_path = os.path.normcase(os.path.realpath(path))
        resolved_parent = os.path.normcase(os.path.realpath(parent))
        return os.path.commonpath((resolved_path, resolved_parent)) == resolved_parent
    except (OSError, ValueError):
        return False


def _validate_install_root(install_root, allowed_parents):
    install_root = os.path.realpath(install_root)
    if os.path.basename(install_root).lower() != "animkey":
        raise UpdateError("The active package is not installed in an AnimKey folder.")

    resolved_parent = os.path.dirname(install_root)
    valid_parents = [os.path.normcase(os.path.realpath(path)) for path in allowed_parents if path]
    if os.path.normcase(resolved_parent) not in valid_parents:
        raise UpdateError(
            "Updates are only enabled for AnimKey installed in Maya's application or scripts folder."
        )
    return install_root, resolved_parent


def resolve_install_root(allowed_parents, active_root=None):
    """Use the installed copy even when a checkout shadows it in sys.path.

    Never replace an external source checkout. A fallback must already contain
    a complete installed package in one of Maya's explicitly allowed folders.
    """
    active_root = active_root or package_root()
    allowed_parents = tuple(path for path in allowed_parents if path)
    candidates = [active_root] + [
        os.path.join(parent, "AnimKey") for parent in allowed_parents
    ]
    for candidate in candidates:
        try:
            candidate, _parent = _validate_install_root(candidate, allowed_parents)
        except UpdateError:
            continue
        if all(os.path.isfile(os.path.join(candidate, name)) for name in REQUIRED_PACKAGE_FILES):
            return candidate
    raise UpdateError(
        "No complete AnimKey installation was found in Maya's application or scripts folder. "
        "Run AnimKey_Install.py once, then try the update again. "
        "Currently loaded from: {}".format(active_root)
    )


def _safe_remove_tree(path, allowed_parent):
    if (
        not path
        or not _is_within(path, allowed_parent)
        or os.path.normcase(os.path.realpath(path))
        == os.path.normcase(os.path.realpath(allowed_parent))
    ):
        raise UpdateError("Refusing to remove an unsafe update path: {}".format(path))
    if os.path.exists(path):
        shutil.rmtree(path)


def _download_archive(release, destination, token=None, opener=None):
    if not release.installable:
        raise UpdateError(
            "AnimKey {} does not include a compatible release package and checksum.".format(
                release.version
            )
        )

    raw = _read_url(
        release.download_url,
        timeout=60,
        token=token,
        opener=opener,
        accept="application/octet-stream",
        max_bytes=MAX_ARCHIVE_BYTES,
    )
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise UpdateError("The release archive is larger than the updater safety limit.")

    digest = hashlib.sha256(raw).hexdigest()
    expected = release.digest
    if not expected and release.checksum_url:
        checksum_text = _read_url(
            release.checksum_url,
            timeout=20,
            token=token,
            opener=opener,
            accept="application/octet-stream",
            max_bytes=1024 * 1024,
        ).decode("utf-8", "replace")
        matching_lines = [
            line for line in checksum_text.splitlines()
            if not release.asset_name or release.asset_name in line
        ]
        match = SHA256_PATTERN.search("\n".join(matching_lines))
        expected = match.group(1).lower() if match else ""
    if not expected:
        raise UpdateError("The release does not provide a valid SHA-256 checksum.")
    if digest.lower() != expected.lower():
        raise UpdateError("The downloaded release failed SHA-256 verification.")

    with open(destination, "wb") as stream:
        stream.write(raw)


def _safe_extract(archive_path, destination):
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError("The downloaded release is not a valid ZIP file: {}".format(exc))

    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise UpdateError("The release archive contains too many files.")
        total_size = sum(member.file_size for member in members)
        if total_size > MAX_ARCHIVE_BYTES:
            raise UpdateError("The expanded release is larger than the updater safety limit.")

        destination_real = os.path.realpath(destination)
        for member in members:
            member_name = member.filename.replace("\\", "/")
            target = os.path.realpath(os.path.join(destination, member_name))
            mode = (member.external_attr >> 16) & 0xFFFF
            if (
                member_name.startswith("/")
                or re.match(r"^[A-Za-z]:", member_name)
                or not _is_within(target, destination_real)
                or stat.S_ISLNK(mode)
            ):
                raise UpdateError("The release archive contains an unsafe path.")
        archive.extractall(destination)


def _find_payload(extracted_root):
    candidates = []
    for current_root, directories, files in os.walk(extracted_root):
        directories[:] = [item for item in directories if item != "__MACOSX"]
        if os.path.basename(current_root).lower() != "animkey":
            continue
        if all(
            os.path.isfile(os.path.join(current_root, item))
            for item in REQUIRED_PACKAGE_FILES
        ):
            candidates.append(current_root)
    if len(candidates) != 1:
        raise UpdateError(
            "The release must contain exactly one complete AnimKey package (found {}).".format(
                len(candidates)
            )
        )
    return candidates[0]


def _version_from_payload(payload_root):
    version_path = os.path.join(payload_root, "version.py")
    try:
        with open(version_path, "r", encoding="utf-8") as stream:
            content = stream.read()
    except OSError as exc:
        raise UpdateError("Could not read the release version: {}".format(exc))
    match = re.search(r'^__version__\s*=\s*[\'\"]([^\'\"]+)[\'\"]', content, re.MULTILINE)
    return normalize_version(match.group(1)) if match else None


def _copy_ignore(_source, names):
    ignored = set()
    for name in names:
        lowered = name.lower()
        if lowered in {"__pycache__", ".git", ".pytest_cache"} or lowered.endswith(
            (".pyc", ".pyo")
        ):
            ignored.add(name)
    return ignored


def install_release(
    release,
    install_root=None,
    allowed_parents=(),
    plugin_destination="",
    token=None,
    opener=None,
):
    """Download, verify, and atomically activate a release.

    The Qt/Maya runtime is reloaded by the caller after this filesystem
    operation completes.
    """
    if not isinstance(release, ReleaseInfo):
        raise UpdateError("Invalid release selection.")
    install_root = install_root or package_root()
    install_root, install_parent = _validate_install_root(install_root, allowed_parents)

    work_dir = tempfile.mkdtemp(prefix=".animkey-update-", dir=install_parent)
    archive_path = os.path.join(work_dir, "release.zip")
    extracted_root = os.path.join(work_dir, "extracted")
    staging_path = install_root + ".installing_{}".format(os.getpid())
    backup_path = install_root + ".backup_{}".format(os.getpid())
    warnings = []

    try:
        _download_archive(release, archive_path, token=token, opener=opener)
        os.makedirs(extracted_root)
        _safe_extract(archive_path, extracted_root)
        payload_root = _find_payload(extracted_root)
        payload_version = _version_from_payload(payload_root)
        if payload_version != release.version:
            raise UpdateError(
                "Release {} contains AnimKey {}.".format(
                    release.version, payload_version or "with no valid version"
                )
            )

        _safe_remove_tree(staging_path, install_parent)
        _safe_remove_tree(backup_path, install_parent)
        shutil.copytree(payload_root, staging_path, ignore=_copy_ignore)
        missing = [
            item for item in REQUIRED_PACKAGE_FILES
            if not os.path.isfile(os.path.join(staging_path, item))
        ]
        if missing:
            raise UpdateError("The staged package is incomplete: {}".format(", ".join(missing)))

        activated = False
        try:
            if os.path.exists(install_root):
                os.replace(install_root, backup_path)
            os.replace(staging_path, install_root)
            activated = True
        except Exception as exc:
            try:
                if not os.path.exists(install_root) and os.path.exists(backup_path):
                    os.replace(backup_path, install_root)
            except Exception:
                pass
            raise UpdateError("Could not activate AnimKey {}: {}".format(release.version, exc))

        if activated and plugin_destination:
            plugin_source = os.path.join(os.path.dirname(payload_root), "AnimKey_plugin.py")
            if os.path.isfile(plugin_source):
                try:
                    plugin_parent = os.path.dirname(plugin_destination)
                    os.makedirs(plugin_parent, exist_ok=True)
                    plugin_temp = plugin_destination + ".{}.tmp".format(os.getpid())
                    shutil.copy2(plugin_source, plugin_temp)
                    os.replace(plugin_temp, plugin_destination)
                except Exception as exc:
                    warnings.append("The Maya plug-in copy could not be updated: {}".format(exc))

        try:
            _safe_remove_tree(backup_path, install_parent)
        except Exception as exc:
            warnings.append("The previous-version backup could not be removed: {}".format(exc))
        return InstallResult(version=release.version, warnings=warnings)
    finally:
        for leftover in (staging_path,):
            try:
                _safe_remove_tree(leftover, install_parent)
            except Exception:
                pass
        try:
            _safe_remove_tree(work_dir, install_parent)
        except Exception:
            pass
