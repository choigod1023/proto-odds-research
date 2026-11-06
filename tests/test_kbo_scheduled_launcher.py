import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("code", [0, 7])
def test_scheduler_preserves_arguments_log_and_exit_code(tmp_path, code):
    launcher = Path(__file__).resolve().parents[1] / "research/kbo_forward/scheduled_run.py"
    runtime, state, m1 = (tmp_path / name for name in ("pinned runtime", "state one", "state two"))
    runner = runtime / "research/kbo_forward/runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        "import ctypes, json, os, sys\n"
        "if sys.platform == 'win32':\n"
        "    console = ctypes.windll.kernel32.GetConsoleWindow\n"
        "    console.restype = ctypes.c_void_p\n"
        "    visible = ctypes.windll.user32.IsWindowVisible\n"
        "    visible.argtypes = (ctypes.c_void_p,)\n"
        "    assert not visible(console())\n"
        "assert os.environ['PYTHONUTF8'] == '1' and os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\n"
        "print(json.dumps(sys.argv[1:]))\n"
        "print('수집 로그', file=sys.stderr)\n"
        f"raise SystemExit({code})\n",
        encoding="utf-8",
    )
    python = Path(sys.executable)
    gui_python = python.with_name("pythonw.exe")
    result = subprocess.run(
        [str(gui_python if gui_python.is_file() else python), str(launcher),
         "--python", str(python), "--runtime", str(runtime),
         "--state", str(state), "--m1-state", str(m1)],
        stdin=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    assert result.returncode == code
    lines = (state / "last-process.log").read_text(encoding="utf-8").splitlines()
    assert "수집 로그" in lines
    argv = next(line for line in lines if line.startswith("["))
    assert json.loads(argv) == ["--state", str(state), "--m1-state", str(m1)]
