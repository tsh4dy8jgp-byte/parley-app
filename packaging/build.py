"""Build the Parley desktop app for this computer: dist/Parley-<version>-<target>.dmg or .zip.

    pip install -e ".[gui,build]"
    python packaging/build.py                      # downloads ffmpeg + ffprobe once (cached in build/)
    python packaging/build.py --ffmpeg-dir DIR     # use your own portable ffmpeg + ffprobe instead

PyInstaller cannot cross-compile: run this on macOS for the Mac app and on Windows for the .exe
(.github/workflows/build.yml does both).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
STAGE = BUILD / "ffmpeg"                  # what parley.spec bundles
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

_RIEDL = "https://ffmpeg.martin-riedl.de/download/macos"
_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
# Portable builds: static on macOS (Homebrew's ffmpeg links to /opt/homebrew and can't be moved),
# LGPL shared DLLs on Windows. Each target's notice names its license and where the source is.
FFMPEG = {
    "macos-arm64": {
        "urls": [f"{_RIEDL}/arm64/1789931890_9.0.2/ffmpeg.zip", f"{_RIEDL}/arm64/1789931890_9.0.2/ffprobe.zip"],
        "license": "GPL version 3 (the build enables GPL and version-3 components)",
        "license_url": "https://www.gnu.org/licenses/gpl-3.0.txt",
        "source": "https://ffmpeg.org/download.html; build scripts: https://git.martin-riedl.de/ffmpeg/build-script",
    },
    "macos-x64": {
        "urls": [f"{_RIEDL}/amd64/1789931006_9.0.2/ffmpeg.zip", f"{_RIEDL}/amd64/1789931006_9.0.2/ffprobe.zip"],
        "license": "GPL version 3 (the build enables GPL and version-3 components)",
        "license_url": "https://www.gnu.org/licenses/gpl-3.0.txt",
        "source": "https://ffmpeg.org/download.html; build scripts: https://git.martin-riedl.de/ffmpeg/build-script",
    },
    "windows-x64": {
        "urls": [f"{_BTBN}/ffmpeg-n9.0-latest-win64-lgpl-shared-9.0.zip"],
        "license": "LGPL version 2.1 or later",
        "license_url": None,      # the archive ships LICENSE.txt
        "source": "https://ffmpeg.org/download.html; build scripts: https://github.com/BtbN/FFmpeg-Builds",
    },
}
EXE = ".exe" if sys.platform == "win32" else ""


def target() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return {"arm64": "macos-arm64", "x86_64": "macos-x64"}[machine]
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "windows-x64"
    raise SystemExit(f"no build for {sys.platform}/{machine}: Parley ships for macOS and Windows x64")


def download(url: str, dest: Path) -> Path:
    if dest.is_file():
        return dest
    print(f"downloading {url}")
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": f"parley-build/{VERSION}"})  # the default one gets 403
    with urllib.request.urlopen(req, context=context, timeout=120) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    tmp.replace(dest)
    return dest


def _wanted(name: str) -> bool:
    if sys.platform == "win32":
        return name.lower() in ("ffmpeg.exe", "ffprobe.exe", "license.txt") or name.lower().endswith(".dll")
    return name in ("ffmpeg", "ffprobe")


def stage_ffmpeg(t: str, own_dir: Path | None) -> None:
    """Fill build/ffmpeg/ with ffmpeg, ffprobe (+ their DLLs) and a NOTICE.txt."""
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    if own_dir:
        for p in own_dir.iterdir():
            if p.is_file() and _wanted(p.name):
                shutil.copy2(p, STAGE / p.name)
        source = f"your own build from {own_dir}"
    else:
        spec = FFMPEG[t]
        for url in spec["urls"]:
            archive = download(url, BUILD / "downloads" / f"{t}-{url.rsplit('/', 1)[1]}")
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    name = info.filename.rsplit("/", 1)[-1]
                    if not info.is_dir() and _wanted(name):
                        (STAGE / name).write_bytes(z.read(info))
        if spec["license_url"]:
            download(spec["license_url"], BUILD / "downloads" / "LICENSE.txt")
            shutil.copy2(BUILD / "downloads" / "LICENSE.txt", STAGE / "LICENSE.txt")
        source = ", ".join(spec["urls"])
    for name in ("ffmpeg", "ffprobe"):
        exe = STAGE / (name + EXE)
        if not exe.is_file():
            raise SystemExit(f"{name}{EXE} missing from {own_dir or source}")
        exe.chmod(0o755)

    version = _run(STAGE / f"ffmpeg{EXE}", "-hide_banner", "-version").splitlines()[0]
    _run(STAGE / f"ffprobe{EXE}", "-hide_banner", "-version")
    if "libmp3lame" not in _run(STAGE / f"ffmpeg{EXE}", "-hide_banner", "-encoders"):
        raise SystemExit("this ffmpeg cannot encode MP3 (no libmp3lame); Parley needs it")
    print(version)
    lines = [f"Parley runs FFmpeg as a separate program, unmodified: {version}",
             f"Downloaded from: {source}"]
    if not own_dir:
        lines += [f"License: {FFMPEG[t]['license']}, see LICENSE.txt and https://ffmpeg.org/legal.html",
                  f"Source code: {FFMPEG[t]['source']}"]
    (STAGE / "NOTICE.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(*cmd, env=None) -> str:
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(f"{' '.join(map(str, cmd))} failed ({proc.returncode}):\n{proc.stderr}{proc.stdout}")
    return proc.stdout


def self_test(program: Path, online: bool) -> None:
    """Run the built app's checks with a bare PATH, as when it is opened from Finder or Explorer."""
    report = BUILD / "self-test.txt"
    report.unlink(missing_ok=True)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [r"C:\Windows\System32", r"C:\Windows"] if sys.platform == "win32" else ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    cmd = [str(program), "--self-test", str(report)] + (["--online"] if online else [])
    proc = subprocess.run(cmd, env=env, timeout=300)
    print(report.read_text(encoding="utf-8") if report.is_file() else "(no self-test report)", end="")
    if proc.returncode != 0:
        raise SystemExit(f"self-test failed (exit {proc.returncode})")


def package(t: str) -> Path:
    if sys.platform == "darwin":
        app = DIST / "Parley.app"
        _run("codesign", "--verify", "--deep", "--strict", app)
        stage = BUILD / "dmg"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        _run("ditto", app, stage / "Parley.app")
        (stage / "Applications").symlink_to("/Applications")
        out = DIST / f"Parley-{VERSION}-{t}.dmg"
        _run("hdiutil", "create", "-volname", "Parley", "-srcfolder", stage, "-ov", "-format", "UDZO", out)
        return out
    out = DIST / f"Parley-{VERSION}-{t}"
    return Path(shutil.make_archive(str(out), "zip", root_dir=DIST, base_dir="Parley"))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ffmpeg-dir", type=Path, help="folder with portable ffmpeg + ffprobe to bundle")
    ap.add_argument("--online", action="store_true", help="self-test also synthesizes one line (network)")
    ap.add_argument("--skip-self-test", action="store_true", help="e.g. on a machine without a display")
    args = ap.parse_args(argv)

    t = target()
    stage_ffmpeg(t, args.ffmpeg_dir)
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "parley.spec"),
                    "--noconfirm", "--clean", "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller")],
                   check=True, cwd=ROOT)
    program = (DIST / "Parley.app" / "Contents" / "MacOS" / "Parley" if sys.platform == "darwin"
               else DIST / "Parley" / f"Parley{EXE}")
    if not args.skip_self_test:
        self_test(program, args.online)
    print(f"built {package(t)}")


if __name__ == "__main__":
    main()
