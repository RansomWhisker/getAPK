"""getapk: pull installed apps from an Android device over adb.

Single-APK apps become `<package>-<version>.apk`. Apps installed as split APKs (most Play Store
apps today) become `<package>-<version>.apks`, a zip holding base.apk and every split, which
AppSleuth and bundletool-style tools understand. Only use it on devices and apps you are
authorised to analyse.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Dict, List, Optional

from . import __version__
from ._color import colorize, paint
from . import banner

PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,100}$")
DEVICE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./=~+@\-]+\.apk$")
ADB_TIMEOUT = 120
PULL_TIMEOUT = 900


_builtin_print = print


def print(*args, **kw):  # noqa: A001  (status lines get colour by prefix; everything else is untouched)
    if len(args) == 1 and isinstance(args[0], str):
        args = (colorize(args[0], kw.get("file")),)
    _builtin_print(*args, **kw)


class AdbError(Exception):
    pass


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


class Adb:
    def __init__(self, serial: Optional[str] = None, exe: Optional[str] = None):
        self.exe = exe or shutil.which("adb")
        if not self.exe:
            raise AdbError("adb not found. Install Android platform-tools and put adb on your PATH "
                           "(or pass --adb /path/to/adb).")
        if serial is not None and not SERIAL_RE.match(serial):
            raise AdbError("invalid device serial")
        self.serial = serial

    def _base(self) -> List[str]:
        return [self.exe] + (["-s", self.serial] if self.serial else [])

    def run(self, *args: str, timeout: int = ADB_TIMEOUT) -> str:
        try:
            r = subprocess.run(self._base() + list(args), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb {' '.join(args[:2])} timed out")
        if r.returncode != 0:
            raise AdbError((r.stderr or r.stdout).strip()[:300] or f"adb exited {r.returncode}")
        return r.stdout

    def devices(self) -> List[Dict[str, str]]:
        out = subprocess.run([self.exe, "devices", "-l"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=ADB_TIMEOUT).stdout
        rows = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
                rows.append({"serial": parts[0], "state": parts[1], "model": model})
        return rows

    def check_device(self):
        ds = self.devices()
        ready = [d for d in ds if d["state"] == "device"]
        if self.serial:
            if not any(d["serial"] == self.serial and d["state"] == "device" for d in ds):
                raise AdbError(f"device {self.serial} not found or not authorised (check the phone for the USB debugging prompt)")
        elif not ready:
            bad = [d for d in ds if d["state"] != "device"]
            raise AdbError("no authorised device. " + (f"{bad[0]['serial']} is '{bad[0]['state']}': "
                           "unlock the phone and accept the USB debugging prompt." if bad else
                           "Connect a device with USB debugging enabled."))
        elif len(ready) > 1:
            raise AdbError("several devices connected; choose one with -s SERIAL: " +
                           ", ".join(f"{d['serial']} ({d['model']})" for d in ready))

    def packages(self, include_system: bool = False) -> List[str]:
        out = self.run("shell", "pm", "list", "packages", *([] if include_system else ["-3"]))
        pk = sorted({l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:")})
        return [p for p in pk if PKG_RE.match(p)]

    def apk_paths(self, package: str) -> List[str]:
        if not PKG_RE.match(package):
            raise AdbError(f"invalid package name: {package!r}")
        out = self.run("shell", "pm", "path", package)
        paths = [l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:")]
        bad = [p for p in paths if not DEVICE_PATH_RE.match(p)]
        if bad:
            raise AdbError(f"unexpected path reported by the device: {bad[0][:80]!r}")
        # base first, then splits, in a stable order
        return sorted(paths, key=lambda p: (os.path.basename(p) != "base.apk", p))

    def version(self, package: str) -> Dict[str, str]:
        info = {"name": "", "code": "", "installer": ""}
        try:
            out = self.run("shell", "dumpsys", "package", package)
        except AdbError:
            return info
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("versionName=") and not info["name"]:
                info["name"] = line.split("=", 1)[1].strip()
            elif line.startswith("versionCode=") and not info["code"]:
                info["code"] = line.split("=", 1)[1].split()[0]
            elif line.startswith("installerPackageName=") and not info["installer"]:
                info["installer"] = line.split("=", 1)[1].strip()
        return info

    def pull(self, remote: str, local: str):
        self.run("pull", remote, local, timeout=PULL_TIMEOUT)


def safe_part(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]", "_", s)[:80] or "unknown"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def looks_like_apk(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return "AndroidManifest.xml" in z.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def pull_app(adb: Adb, package: str, outdir: str, fmt: str = "auto", quiet: bool = False) -> Dict[str, object]:
    paths = adb.apk_paths(package)
    if not paths:
        raise AdbError(f"{package} is not installed")
    ver = adb.version(package)
    stem = safe_part(package) + (f"-{safe_part(ver['name'])}" if ver["name"] else "")
    os.makedirs(outdir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="getapk_")
    try:
        local = []
        for i, remote in enumerate(paths):
            name = safe_part(os.path.basename(remote))
            if name in [os.path.basename(x) for x in local]:
                name = f"{i}_{name}"
            dst = os.path.join(work, name)
            adb.pull(remote, dst)
            if not looks_like_apk(dst):
                raise AdbError(f"{name} did not pull as a valid APK")
            local.append(dst)
        split = len(local) > 1
        if fmt == "apk" and split:
            raise AdbError(f"{package} is a split-APK app ({len(local)} files); use --format auto or apks")
        if split or fmt == "apks":
            final = os.path.join(outdir, stem + ".apks")
            tmp_out = final + ".part"
            with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_STORED) as z:       # APKs are already compressed
                for f in local:
                    z.write(f, os.path.basename(f))
            os.replace(tmp_out, final)
        else:
            final = os.path.join(outdir, stem + ".apk")
            shutil.copyfile(local[0], final)
        return {"package": package, "path": final, "splits": len(local), "version": ver["name"],
                "size": os.path.getsize(final), "sha256": sha256_file(final), "installer": ver["installer"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fmt_size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def cmd_list(adb: Adb, a) -> int:
    pk = adb.packages(a.include_system)
    if a.match:
        pk = [p for p in pk if a.match.lower() in p.lower()]
    if not pk:
        print(paint("No matching apps.", "yellow"))
        return 1
    for i, p in enumerate(pk, 1):
        v = adb.version(p)
        print(f"{paint(f'{i:3}.', 'cyan')} {paint(f'{p:<55}', 'bold')} {v['name'] or '?':<14} {paint(v['installer'] or '', 'dim')}")
    return 0


def main(argv: Optional[List[str]] = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    banner.show("getAPK", __version__, "https://github.com/RansomWhisker/getAPK",
                "pull installed apps from an Android device", suppress=banner.strip_flag(argv))
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="getapk", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", nargs="*", help="package name(s) to pull, e.g. com.example.app")
    ap.add_argument("-l", "--list", action="store_true", help="list installed apps")
    ap.add_argument("--all", action="store_true", help="pull every (user-installed) app")
    ap.add_argument("-m", "--match", help="with --list/--all: only packages containing this text")
    ap.add_argument("--include-system", action="store_true", help="include system/preinstalled apps")
    ap.add_argument("-s", "--serial", help="device serial (needed when several devices are connected)")
    ap.add_argument("-o", "--out", default=".", help="output directory (default: current)")
    ap.add_argument("--format", choices=["auto", "apk", "apks"], default="auto",
                    help="auto: .apk for single-APK apps, .apks bundle for split apps")
    ap.add_argument("--adb", help="path to the adb executable")
    ap.add_argument("--version", action="version", version=f"getapk {__version__}")
    a = ap.parse_args(argv)
    try:
        adb = Adb(a.serial, a.adb)
        adb.check_device()
        if a.list:
            sys.exit(cmd_list(adb, a))
        targets = list(a.package)
        if a.all:
            targets += [p for p in adb.packages(a.include_system)
                        if (not a.match or a.match.lower() in p.lower()) and p not in targets]
        if not targets:
            ap.print_usage(sys.stderr)
            die("give a package name, or use --list / --all", 2)
        failures = 0
        for p in targets:
            try:
                r = pull_app(adb, p, a.out, a.format)
                kind = f"{r['splits']} splits" if r["splits"] > 1 else "single APK"
                print(f"{paint('[+]', 'green')} {paint(r['package'], 'bold')} {r['version'] or ''} -> {paint(r['path'], 'cyan')}  "
                      f"({paint(kind, 'dim')}, {fmt_size(r['size'])})")
                _builtin_print(paint(f"    sha256 {r['sha256']}", "dim"))
            except AdbError as e:
                failures += 1
                print(f"[-] {p}: {e}", file=sys.stderr)
        if failures:
            sys.exit(1)
    except AdbError as e:
        die(str(e))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
