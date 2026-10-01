# PyInstaller spec for the Parley desktop app. Run it through packaging/build.py, which first
# puts ffmpeg + ffprobe in build/ffmpeg/. customtkinter and tkinterdnd2 data come from the
# hooks in pyinstaller-hooks-contrib.
import sys
import tomllib
from pathlib import Path

ROOT = Path(SPECPATH).parent
FFMPEG = ROOT / "build" / "ffmpeg"
ICON = str(ROOT / "src" / "parley" / "parley.ico")
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

if not (FFMPEG / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")).is_file():
    raise SystemExit(f"{FFMPEG} has no ffmpeg: run packaging/build.py, not PyInstaller directly")

programs = [(str(p), "ffmpeg") for p in FFMPEG.iterdir() if p.suffix.lower() != ".txt"]
notices = [(str(p), "ffmpeg") for p in FFMPEG.glob("*.txt")]
# Windows: copy ffmpeg as data, or the DLL analysis puts a second copy of its DLLs at the top level.
# macOS: as binaries, so they land in Contents/Frameworks and are signed with the bundle.
programs, program_data = ([], programs) if sys.platform == "win32" else (programs, [])

a = Analysis(
    [str(ROOT / "packaging" / "parley_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=programs,
    datas=[(ICON, "parley")] + notices + program_data,
    excludes=["pytest", "_pytest", "PIL", "PyInstaller"],   # PIL: customtkinter's optional CTkImage
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Parley",
    console=False,
    upx=False,
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Parley", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Parley.app",
        icon=ICON,                       # converted to .icns with Pillow
        bundle_identifier="app.parley.desktop",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Parley",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
        },
    )
