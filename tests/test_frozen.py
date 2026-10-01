"""The packaged-app set-up (parley.frozen): bundled ffmpeg on PATH, hidden consoles, self-test."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from parley import frozen


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """Pretend to run from a PyInstaller build in tmp_path, with an ffmpeg/ folder."""
    (tmp_path / "ffmpeg").mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)   # restored after the test
    return tmp_path


def test_prepare_does_nothing_from_source(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)
    before = os.environ["PATH"]
    frozen.prepare()
    assert frozen.bundle_dir() is None
    assert os.environ["PATH"] == before
    assert subprocess.Popen is not frozen._NoWindowPopen


def test_prepare_puts_bundled_ffmpeg_first_once(bundle):
    frozen.prepare()
    frozen.prepare()
    assert os.environ["PATH"].split(os.pathsep) == [str(bundle / "ffmpeg"), "/usr/bin", "/bin"]


def test_prepare_hides_consoles_only_on_windows(bundle, monkeypatch):
    frozen.prepare()
    assert subprocess.Popen is not frozen._NoWindowPopen
    monkeypatch.setattr(sys, "platform", "win32")
    frozen.prepare()
    assert subprocess.Popen is frozen._NoWindowPopen


def test_no_window_popen_adds_the_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess.Popen, "__init__", lambda self, *a, **k: seen.update(k))
    frozen._NoWindowPopen(["x"])
    frozen._NoWindowPopen(["x"], creationflags=0x10)
    assert seen["creationflags"] == 0x10 | frozen.CREATE_NO_WINDOW


def test_import_does_not_load_pydub():
    """prepare() has to run before pydub is imported, so importing frozen must not import it."""
    code = ("import sys, parley.frozen\n"
            "loaded = {'pydub', 'tkinter', 'customtkinter'} & set(sys.modules)\n"
            "assert not loaded, loaded")
    src = Path(__file__).resolve().parents[1] / "src"   # cwd=src so the root parley.py can't shadow the package
    r = subprocess.run([sys.executable, "-c", code], cwd=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_self_test_passes_from_source(tmp_path):
    pytest.importorskip("customtkinter")
    pytest.importorskip("tkinterdnd2")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    import tkinter as tk
    try:
        tk.Tk().destroy()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    report = tmp_path / "report.txt"
    code = frozen.self_test(report)
    assert code == 0, report.read_text()
    assert report.read_text().splitlines()[-1] == "PASS"


def test_self_test_reports_every_failure(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("broken")
    monkeypatch.setattr(frozen, "CHECKS", [("one", boom), ("two", lambda: "fine"), ("three", boom)])
    assert frozen.self_test(tmp_path / "r.txt") == 1
    assert (tmp_path / "r.txt").read_text().splitlines() == [
        "FAIL  one: RuntimeError: broken", "ok    two: fine", "FAIL  three: RuntimeError: broken",
        "2 of 3 checks failed"]
