import os
import tempfile
import unittest
import zipfile

from getapk_cli import cli


def make_apk(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", b"x")


class FakeAdb(cli.Adb):
    def __init__(self, paths, bad_apk=False):
        self.exe, self.serial = "adb", None
        self._paths, self._bad = paths, bad_apk

    def run(self, *args, timeout=0):
        if args[:2] == ("shell", "pm") and args[2] == "path":
            return "".join(f"package:{p}\n" for p in self._paths)
        if args[:2] == ("shell", "dumpsys"):
            return "    versionCode=12 minSdk=24\n    versionName=1.2.3\n    installerPackageName=com.android.vending\n"
        if args[0] == "pull":
            if self._bad:
                open(args[2], "wb").write(b"not a zip")
            else:
                make_apk(args[2])
            return ""
        raise AssertionError(args)


class Pull(unittest.TestCase):
    def test_single_apk(self):
        out = tempfile.mkdtemp()
        r = cli.pull_app(FakeAdb(["/data/app/x/base.apk"]), "com.example.app", out)
        self.assertTrue(r["path"].endswith("com.example.app-1.2.3.apk"))
        self.assertEqual(r["splits"], 1)

    def test_split_becomes_apks_base_first(self):
        out = tempfile.mkdtemp()
        paths = ["/data/app/x/split_config.en.apk", "/data/app/x/base.apk", "/data/app/x/split_config.arm64_v8a.apk"]
        r = cli.pull_app(FakeAdb(paths), "com.example.app", out)
        self.assertTrue(r["path"].endswith(".apks"))
        with zipfile.ZipFile(r["path"]) as z:
            self.assertEqual(z.namelist()[0], "base.apk")
            self.assertEqual(len(z.namelist()), 3)

    def test_rejects_non_apk(self):
        with self.assertRaises(cli.AdbError):
            cli.pull_app(FakeAdb(["/data/app/x/base.apk"], bad_apk=True), "com.example.app", tempfile.mkdtemp())

    def test_format_apk_refuses_split(self):
        with self.assertRaises(cli.AdbError):
            cli.pull_app(FakeAdb(["/data/app/x/base.apk", "/data/app/x/split_a.apk"]), "com.example.app",
                         tempfile.mkdtemp(), fmt="apk")


class Validation(unittest.TestCase):
    def test_package_names(self):
        for bad in ("a; rm -rf /", "$(id)", "com.example.app && x", "../x", "single", ""):
            with self.assertRaises(cli.AdbError, msg=bad):
                FakeAdb([]).apk_paths(bad)

    def test_hostile_device_path_rejected(self):
        with self.assertRaises(cli.AdbError):
            FakeAdb(["/data/app/x/base.apk; id"]).apk_paths("com.example.app")
        with self.assertRaises(cli.AdbError):
            FakeAdb(["../../etc/passwd"]).apk_paths("com.example.app")

    def test_serial_and_safe_name(self):
        with self.assertRaises(cli.AdbError):
            cli.Adb("x; y", exe="adb")
        self.assertEqual(cli.safe_part("../../a b"), ".._.._a_b")
        self.assertNotIn("/", cli.safe_part("a/b"))


if __name__ == "__main__":
    unittest.main()
