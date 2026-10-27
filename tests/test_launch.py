"""`python app.py` (VS Code's Run button) must start the app, not crash in bare mode."""

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=20)


def test_plain_python_launch_serves_the_app(tmp_path):
    port = _free_port()
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
              else {"start_new_session": True})
    # Launched from an unrelated cwd, as a terminal or IDE might.
    proc = subprocess.Popen([sys.executable, str(ROOT / "app.py"), "--server.headless", "true",
                             "--server.port", str(port)], cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    try:
        deadline, health = time.time() + 60, None
        while time.time() < deadline and proc.poll() is None:
            try:
                health = urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2).read()
                break
            except OSError:
                time.sleep(0.5)
        assert proc.poll() is None, proc.stdout.read().decode(errors="replace")
        assert health == b"ok"
        favicon = urllib.request.urlopen(f"http://127.0.0.1:{port}/app/static/favicon.png", timeout=5)
        assert favicon.status == 200
    finally:
        _kill_tree(proc)
