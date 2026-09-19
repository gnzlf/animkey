"""Build the verified ZIP consumed by AnimKey's in-app updater."""

from __future__ import absolute_import

import argparse
import hashlib
import os
import re
import sys
import zipfile


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_FILE_PATTERN = re.compile(
    r'^__version__\s*=\s*[\'\"]([^\'\"]+)[\'\"]', re.MULTILINE
)
SKIPPED_DIRECTORIES = {"__pycache__", ".git", ".pytest_cache"}
SKIPPED_SUFFIXES = (".pyc", ".pyo")
TOP_LEVEL_FILES = ("AnimKey_Install.py", "AnimKey_plugin.py", "README.md")


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def source_version(root):
    version_path = os.path.join(root, "AnimKey", "version.py")
    with open(version_path, "r", encoding="utf-8") as stream:
        match = VERSION_FILE_PATTERN.search(stream.read())
    if not match:
        raise RuntimeError("AnimKey/version.py does not define __version__")
    return match.group(1)


def iter_package_files(package_root):
    for current_root, directories, filenames in os.walk(package_root):
        directories[:] = sorted(
            name for name in directories if name.lower() not in SKIPPED_DIRECTORIES
        )
        for filename in sorted(filenames):
            if filename.lower().endswith(SKIPPED_SUFFIXES):
                continue
            yield os.path.join(current_root, filename)


def build_release(version, output_directory):
    if not VERSION_PATTERN.match(version):
        raise RuntimeError("Version must use MAJOR.MINOR.PATCH, for example 1.2.0")

    root = repository_root()
    actual_version = source_version(root)
    if actual_version != version:
        raise RuntimeError(
            "Requested version {} does not match AnimKey/version.py ({})".format(
                version, actual_version
            )
        )

    package_root = os.path.join(root, "AnimKey")
    missing = [
        name for name in TOP_LEVEL_FILES
        if not os.path.isfile(os.path.join(root, name))
    ]
    if missing:
        raise RuntimeError("Missing release files: {}".format(", ".join(missing)))

    os.makedirs(output_directory, exist_ok=True)
    archive_name = "AnimKey-{}.zip".format(version)
    archive_path = os.path.join(output_directory, archive_name)
    if os.path.exists(archive_path):
        os.remove(archive_path)

    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for source_path in iter_package_files(package_root):
            archive_name_in_zip = os.path.relpath(source_path, root).replace(os.sep, "/")
            archive.write(source_path, archive_name_in_zip)
        for filename in TOP_LEVEL_FILES:
            archive.write(os.path.join(root, filename), filename)

    digest = hashlib.sha256()
    with open(archive_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_path = archive_path + ".sha256"
    with open(checksum_path, "w", encoding="ascii", newline="\n") as stream:
        stream.write("{}  {}\n".format(digest.hexdigest(), os.path.basename(archive_path)))
    return archive_path, checksum_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", default="dist")
    args = parser.parse_args(argv)
    archive_path, checksum_path = build_release(args.version, args.output)
    print(archive_path)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
