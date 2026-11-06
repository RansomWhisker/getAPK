# getAPK

**Pull an installed app off your Android phone as an `.apk` (or an `.apks` bundle for split apps) — in one command.**

`getapk` talks to your device through `adb`, finds where an app is installed, copies every APK file it consists of, checks each one is a real APK, and writes a single ready-to-analyse file with its SHA-256. It is the Android counterpart of [getIPA](https://github.com/RansomWhisker/getIPA).

> A tool by [**rudranetra.com**](https://rudranetra.com) · website: [rudranetra.com/tools/getapk](https://rudranetra.com/tools/getapk/) · more tools at [rudranetra.com/tools](https://rudranetra.com/tools)

## Why not just `adb pull`?

Most Play Store apps are installed as *split APKs*: `base.apk` plus one file per CPU architecture, language and screen density. Pulling only `base.apk` gives you something that will not install and is missing native libraries and resources. getapk pulls them all and bundles them, base first, into one `.apks` file that [AppSleuth](https://github.com/RansomWhisker/AppSleuth) and bundletool-style tools understand.

## Install

    pipx install getapk

Requires Python 3.9+ and Android platform-tools (`adb`) on your PATH. Works on macOS, Linux and Windows. No Python dependencies.

On the phone: enable Developer options → USB debugging, connect, and accept the authorisation prompt. No root needed.

## Use

    getapk --list                         # user-installed apps with versions and installer
    getapk --list -m bank                 # filter by text
    getapk com.example.app -o ./apps      # one app
    getapk com.a.app com.b.app            # several
    getapk --all -o ./apps                # every user-installed app
    getapk --all --include-system         # include preinstalled apps
    getapk -s SERIAL com.example.app      # pick a device when several are connected

Single-APK apps are written as `<package>-<version>.apk`; split apps as `<package>-<version>.apks`. Use `--format apks` to always bundle. Then:

    appsleuth scan com.example.app-1.2.3.apks

## Security

* Package names, device serials and every path reported by the device are validated before use; nothing is passed through a shell on your computer, and the one command run on the phone only ever receives a validated package name.
* Output file names are sanitised, so a hostile device cannot write outside the output directory.
* Each pulled file must be a valid APK (a zip containing `AndroidManifest.xml`).
* Files are written to a temporary directory and moved into place only when complete.

Only use getapk on devices and apps you own or are authorised to analyse. Apps may be copyrighted; extraction is for your own security analysis.

## Banner and colour

On a terminal each run starts with a short logo, the version and links. It goes to stderr, so piped output and `--json` stay clean. Hide it with `--no-banner` or `NO_BANNER=1`; `NO_COLOR=1` turns colour off and `NO_ANIM=1` skips the animation.

## Tests

    python -m unittest discover -s tests -t .

MIT licence · © Rudranetra India Private Limited
