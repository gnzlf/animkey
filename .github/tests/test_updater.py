import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

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


def make_installed_package(parent, version="1.0.0"):
    root = os.path.join(parent, "AnimKey")
    for name in updater.REQUIRED_PACKAGE_FILES:
        target = os.path.join(root, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as stream:
            stream.write('__version__ = "{}"\n'.format(version))
    return root


class UpdaterTests(unittest.TestCase):
    def test_installed_package_is_preferred_to_external_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "maya")
            installed = make_installed_package(app_dir)
            checkout = make_installed_package(os.path.join(directory, "checkout"))
            with mock.patch.object(updater, "package_root", return_value=checkout):
                actual = updater.resolve_install_root((app_dir,))
            self.assertEqual(actual, os.path.realpath(installed))

    def test_loaded_scripts_install_is_not_redirected_to_another_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "maya")
            make_installed_package(app_dir)
            scripts_dir = os.path.join(app_dir, "2024", "scripts")
            installed = make_installed_package(scripts_dir)
            self.assertEqual(
                updater.resolve_install_root((app_dir, scripts_dir), installed),
                os.path.realpath(installed),
            )

    def test_missing_install_does_not_select_or_replace_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = make_installed_package(os.path.join(directory, "checkout"))
            with self.assertRaisesRegex(updater.UpdateError, "Run AnimKey_Install.py"):
                updater.resolve_install_root((os.path.join(directory, "maya"),), checkout)
            self.assertTrue(os.path.isfile(os.path.join(checkout, "version.py")))

    def test_incomplete_install_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = make_installed_package(directory)
            os.remove(os.path.join(root, "core", "toolbar.py"))
            with self.assertRaisesRegex(updater.UpdateError, "No complete AnimKey"):
                updater.resolve_install_root((directory,), root)

    def test_upgrade_and_downgrade_leave_checkout_and_preferences_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            app_dir = os.path.join(directory, "maya")
            installed = make_installed_package(app_dir)
            checkout = make_installed_package(os.path.join(directory, "checkout"), "9.0.0")
            preferences = os.path.join(app_dir, "AnimKey_user_data", "preferences.json")
            os.makedirs(os.path.dirname(preferences))
            with open(preferences, "w", encoding="utf-8") as stream:
                stream.write('{"theme": "maya_classic"}')
            for version in ("1.1.1", "1.0.0"):
                archive_bytes = make_release_archive(version)
                release = updater.ReleaseInfo(
                    version=version, tag_name="v" + version, name="AnimKey " + version,
                    published_at="", notes="", html_url="",
                    download_url="https://example.test/package.zip",
                    digest=hashlib.sha256(archive_bytes).hexdigest(),
                )
                selected = updater.resolve_install_root((app_dir,), checkout)
                updater.install_release(
                    release, install_root=selected, allowed_parents=(app_dir,),
                    opener=fake_opener({release.download_url: archive_bytes}),
                )
                self.assertEqual(updater._version_from_payload(installed), version)
                self.assertEqual(updater._version_from_payload(checkout), "9.0.0")
                with open(preferences, encoding="utf-8") as stream:
                    self.assertEqual(json.load(stream), {"theme": "maya_classic"})

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
