import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile

from AnimKey.core import updater


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self, size=-1):
        return self.payload if size is None or size < 0 else self.payload[:size]

    def close(self):
        pass


def fake_opener(mapping):
    def open_request(request, timeout=0):
        return FakeResponse(mapping[request.full_url])

    return open_request


def make_release_archive(version):
    output = io.BytesIO()
    files = {
        "bundle/AnimKey/__init__.py": "from AnimKey.version import __version__\n",
        "bundle/AnimKey/version.py": '__version__ = "{}"\n'.format(version),
        "bundle/AnimKey/core/toolbar.py": "UPDATED = True\n",
        "bundle/AnimKey/mods/maya_compat.py": "PYSIDE_MAJOR = 6\n",
        "bundle/AnimKey/new_file.txt": "new release",
        "bundle/AnimKey_plugin.py": "PLUGIN_VERSION = {!r}\n".format(version),
    }
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class UpdaterTests(unittest.TestCase):
    def test_parse_releases_filters_non_stable_and_sorts_versions(self):
        payload = [
            {
                "tag_name": "v1.2.0",
                "name": "AnimKey 1.2.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-09-18T10:00:00Z",
                "assets": [
                    {
                        "name": "AnimKey-1.2.0.zip",
                        "browser_download_url": "https://example.test/1.2.0.zip",
                        "digest": "sha256:" + "a" * 64,
                    }
                ],
            },
            {
                "tag_name": "v2.0.0",
                "draft": False,
                "prerelease": True,
                "assets": [],
            },
            {
                "tag_name": "v1.0.0",
                "draft": False,
                "prerelease": False,
                "assets": [],
            },
        ]

        releases = updater.parse_releases(payload)

        self.assertEqual([release.version for release in releases], ["1.2.0", "1.0.0"])
        self.assertTrue(releases[0].installable)
        self.assertFalse(releases[1].installable)

    def test_fetch_releases_uses_api_payload(self):
        payload = json.dumps(
            [{"tag_name": "v1.0.0", "draft": False, "prerelease": False, "assets": []}]
        ).encode("utf-8")

        releases = updater.fetch_releases(
            opener=fake_opener({"https://example.test/releases": payload}),
            api_url="https://example.test/releases",
        )

        self.assertEqual([release.version for release in releases], ["1.0.0"])

    def test_install_release_atomically_replaces_package_and_plugin(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            install_root = os.path.join(temporary_directory, "AnimKey")
            os.makedirs(install_root)
            with open(os.path.join(install_root, "old_file.txt"), "w", encoding="utf-8") as stream:
                stream.write("old")
            plugin_destination = os.path.join(
                temporary_directory, "plug-ins", "AnimKey_plugin.py"
            )
            archive_bytes = make_release_archive("1.1.0")
            digest = hashlib.sha256(archive_bytes).hexdigest()
            release = updater.ReleaseInfo(
                version="1.1.0",
                tag_name="v1.1.0",
                name="AnimKey 1.1.0",
                published_at="",
                notes="",
                html_url="",
                download_url="https://example.test/AnimKey-1.1.0.zip",
                digest=digest,
                asset_name="AnimKey-1.1.0.zip",
            )

            result = updater.install_release(
                release,
                install_root=install_root,
                allowed_parents=(temporary_directory,),
                plugin_destination=plugin_destination,
                opener=fake_opener({release.download_url: archive_bytes}),
            )

            self.assertEqual(result.version, "1.1.0")
            self.assertFalse(os.path.exists(os.path.join(install_root, "old_file.txt")))
            with open(os.path.join(install_root, "new_file.txt"), encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "new release")
            with open(plugin_destination, encoding="utf-8") as stream:
                self.assertIn("1.1.0", stream.read())
            self.assertFalse(
                any(name.startswith("AnimKey.backup_") for name in os.listdir(temporary_directory))
            )

    def test_bad_checksum_leaves_existing_install_untouched(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            install_root = os.path.join(temporary_directory, "AnimKey")
            os.makedirs(install_root)
            old_file = os.path.join(install_root, "old_file.txt")
            with open(old_file, "w", encoding="utf-8") as stream:
                stream.write("keep me")
            archive_bytes = make_release_archive("1.1.0")
            release = updater.ReleaseInfo(
                version="1.1.0",
                tag_name="v1.1.0",
                name="AnimKey 1.1.0",
                published_at="",
                notes="",
                html_url="",
                download_url="https://example.test/AnimKey-1.1.0.zip",
                digest="0" * 64,
                asset_name="AnimKey-1.1.0.zip",
            )

            with self.assertRaisesRegex(updater.UpdateError, "SHA-256"):
                updater.install_release(
                    release,
                    install_root=install_root,
                    allowed_parents=(temporary_directory,),
                    opener=fake_opener({release.download_url: archive_bytes}),
                )

            with open(old_file, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), "keep me")


if __name__ == "__main__":
    unittest.main()
