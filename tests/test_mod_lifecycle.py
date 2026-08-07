import copy
import importlib.util
import io
import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = pathlib.Path(__file__).parents[1]


def archive_bytes(files, *, symlink=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
    return output.getvalue()


class ModLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.install = self.root / "server"
        self.state = self.root / "state"
        self.win64 = self.install / "Pal/Binaries/Win64"
        self.win64.mkdir(parents=True)
        self.state.mkdir()
        (self.install / "PalServer.exe").write_bytes(b"launcher")
        (self.win64 / "PalServer-Win64-Shipping-Cmd.exe").write_bytes(b"shipping")
        self.environment = mock.patch.dict(os.environ, {
            "PALWORLD_INSTALL_DIR": str(self.install),
            "PALWORLD_STATE_DIR": str(self.state),
            "PALWORLD_RUNTIME": "wine-windows",
        })
        self.environment.start()
        spec = importlib.util.spec_from_file_location(
            f"mod_lifecycle_test_{id(self)}", ROOT / "scripts/mod-lifecycle.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.active = mock.patch.object(self.module, "server_active", return_value=False)
        self.active.start()

    def tearDown(self):
        self.active.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def artifact(self, name, files, *, managed=None, required=None, seed=()):
        value = archive_bytes(files)
        metadata = {
            "version": "fixture-v1", "source": "fixture/source@v1",
            "url": "https://github.com/fixture/release.zip",
            "sha256": self.module.sha256_bytes(value),
            "managed": tuple(managed or files),
            "required": tuple(required or files),
            "seed": tuple(seed),
        }
        self.module.ARTIFACTS = copy.deepcopy(self.module.ARTIFACTS)
        self.module.ARTIFACTS[name] = metadata
        return value

    def install_fixture(self, name, value):
        reviewed = self.module.plan(name, "install")
        with mock.patch.object(self.module, "download", return_value=value):
            return self.module.install_component(
                name, reviewed["plan_hash"], reviewed["confirmation"],
            )

    def test_paldefender_install_remove_and_rollback_preserve_generated_configuration(self):
        value = self.artifact(
            "paldefender", {"PalDefender.dll": b"plugin", "d3d9.dll": b"proxy"},
        )
        reviewed = self.module.plan("paldefender", "install")
        with mock.patch.object(self.module, "download", return_value=value):
            with self.assertRaisesRegex(ValueError, "exact confirmation"):
                self.module.install_component("paldefender", reviewed["plan_hash"], "yes")
        self.assertFalse((self.win64 / "PalDefender.dll").exists())

        result = self.install_fixture("paldefender", value)
        self.assertEqual(result["target_version"], "fixture-v1")
        self.assertEqual((self.win64 / "PalDefender.dll").read_bytes(), b"plugin")
        registry = json.loads((self.state / "mod-lifecycle.json").read_text())
        self.assertEqual(registry["components"]["paldefender"]["version"], "fixture-v1")
        generated = self.win64 / "PalDefender/Config.json"
        generated.parent.mkdir()
        generated.write_text('{"operator":"keep"}')

        removal = self.module.plan("paldefender", "remove")
        self.module.remove_component("paldefender", removal["plan_hash"], removal["confirmation"])
        self.assertFalse((self.win64 / "PalDefender.dll").exists())
        self.assertEqual(generated.read_text(), '{"operator":"keep"}')

        rollback = self.module.plan("paldefender", "rollback")
        self.module.rollback_component("paldefender", rollback["plan_hash"], rollback["confirmation"])
        self.assertEqual((self.win64 / "PalDefender.dll").read_bytes(), b"plugin")
        self.assertEqual(generated.read_text(), '{"operator":"keep"}')

    def test_ue4ss_update_preserves_settings_and_lua_tree(self):
        files = {
            "dwmapi.dll": b"proxy-one",
            "ue4ss/UE4SS.dll": b"loader-one",
            "ue4ss/MemberVariableLayout.ini": b"layout-one",
            "ue4ss/LICENSE": b"license",
            "ue4ss/UE4SS-settings.ini": b"setting=default\n",
            "ue4ss/Mods/mods.txt": b"BuiltIn : 1\n",
            "ue4ss/Mods/BuiltIn/Scripts/main.lua": b"print('built in')\n",
        }
        value = self.artifact(
            "ue4ss", files,
            managed=("dwmapi.dll", "ue4ss/UE4SS.dll", "ue4ss/MemberVariableLayout.ini", "ue4ss/LICENSE"),
            required=tuple(files), seed=("ue4ss/UE4SS-settings.ini", "ue4ss/Mods/"),
        )
        self.install_fixture("ue4ss", value)
        settings = self.win64 / "ue4ss/UE4SS-settings.ini"
        mods = self.win64 / "ue4ss/Mods/mods.txt"
        custom = self.win64 / "ue4ss/Mods/Operator/Scripts/main.lua"
        settings.write_text("setting=operator\n")
        mods.write_text("BuiltIn : 0\nOperator : 1\n")
        custom.parent.mkdir(parents=True)
        custom.write_text("print('operator')\n")

        files["dwmapi.dll"] = b"proxy-two"
        files["ue4ss/UE4SS.dll"] = b"loader-two"
        update = self.artifact(
            "ue4ss", files,
            managed=("dwmapi.dll", "ue4ss/UE4SS.dll", "ue4ss/MemberVariableLayout.ini", "ue4ss/LICENSE"),
            required=tuple(files), seed=("ue4ss/UE4SS-settings.ini", "ue4ss/Mods/"),
        )
        self.install_fixture("ue4ss", update)
        self.assertEqual((self.win64 / "ue4ss/UE4SS.dll").read_bytes(), b"loader-two")
        self.assertEqual(settings.read_text(), "setting=operator\n")
        self.assertEqual(mods.read_text(), "BuiltIn : 0\nOperator : 1\n")
        self.assertEqual(custom.read_text(), "print('operator')\n")

    def test_drift_active_server_and_unsafe_archives_fail_closed(self):
        value = self.artifact(
            "paldefender", {"PalDefender.dll": b"plugin", "d3d9.dll": b"proxy"},
        )
        self.install_fixture("paldefender", value)
        (self.win64 / "PalDefender.dll").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "drifted"):
            self.module.plan("paldefender", "remove")

        (self.win64 / "PalDefender.dll").write_bytes(b"plugin")
        reviewed = self.module.plan("paldefender", "install")
        with mock.patch.object(self.module, "server_active", return_value=True):
            with self.assertRaisesRegex(ValueError, "plan changed|must be stopped"):
                self.module.install_component("paldefender", reviewed["plan_hash"], reviewed["confirmation"])

        bad = archive_bytes({"PalDefender.dll": b"plugin", "../d3d9.dll": b"proxy"})
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.module.safe_archive(bad, self.module.ARTIFACTS["paldefender"])
        linked = archive_bytes(
            {"PalDefender.dll": b"plugin", "d3d9.dll": b"proxy"}, symlink="escape.dll",
        )
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.module.safe_archive(linked, self.module.ARTIFACTS["paldefender"])

    def test_native_runtime_and_unmanaged_removal_are_refused(self):
        self.module.RUNTIME = "native-linux"
        with self.assertRaisesRegex(ValueError, "PALWORLD_RUNTIME=wine-windows"):
            self.module.plan("paldefender", "install")
        self.module.RUNTIME = "wine-windows"
        (self.win64 / "PalDefender.dll").write_bytes(b"unmanaged")
        (self.win64 / "d3d9.dll").write_bytes(b"unmanaged")
        with self.assertRaisesRegex(ValueError, "not managed"):
            self.module.plan("paldefender", "remove")


if __name__ == "__main__":
    unittest.main()
