#!/usr/bin/env python3
"""Launch a built Linux AppImage on Xvfb and check its bundled server.

This checks the packaged app without a headset or a desktop login session.
The profile and display are temporary, and every process started by this check
is stopped before it exits.
"""

import hashlib
import http.client
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def server_port(profile):
    config_dir = profile / "config"
    if not config_dir.is_dir():
        return None
    for log in config_dir.rglob("server.log"):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ports = re.findall(r"--port ([0-9]+)", text)
        for raw in reversed(ports):
            port = int(raw)
            if 1 <= port <= 65535:
                return port
    return None


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def port_closed(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return False
    except OSError:
        return True


def check(appimage):
    if not appimage.is_file() or not os.access(appimage, os.X_OK):
        raise RuntimeError(f"AppImage is missing or cannot run: {appimage}")
    if not shutil.which("xvfb-run"):
        raise RuntimeError("xvfb-run is required for the AppImage smoke check")
    digest = _sha256_file(appimage)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="frame-appimage-smoke-") as directory:
        profile = Path(directory)
        (profile / "tmp").mkdir()
        env = dict(os.environ)
        env.update(
            APPIMAGE_EXTRACT_AND_RUN="1", ELECTRON_DISABLE_SANDBOX="1",
            TMPDIR=str(profile / "tmp"),
            XDG_CONFIG_HOME=str(profile / "config"),
            XDG_CACHE_HOME=str(profile / "cache"),
            XDG_DATA_HOME=str(profile / "data"),
            FRAME_ALIAS="frame-control-appimage-smoke-no-headset",
            FRAME_CONTROL_TELEMETRY="0", FRAME_CONTROL_NO_UPDATE_CHECK="1",
            DO_NOT_TRACK="1",
        )
        output = profile / "app.log"
        port = None
        passed = False
        with output.open("w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen(
                ["xvfb-run", "-a", "-s", "-screen 0 1280x800x24 -nolisten tcp",
                 str(appimage), "--no-sandbox"],
                cwd=profile, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError(f"AppImage exited {proc.returncode} before its server answered")
                    port = server_port(profile)
                    if port is not None:
                        try:
                            with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                                if response.status == 200 and response.headers.get("Server", "").startswith("FrameControl/1"):
                                    passed = True
                                    break
                        except (OSError, urllib.error.URLError, http.client.HTTPException):
                            pass
                    time.sleep(0.5)
                if not passed:
                    raise RuntimeError("AppImage server did not answer within 60 seconds")
            except BaseException:
                log.flush()
                try:
                    print(output.read_text(encoding="utf-8", errors="replace")[-1500:], file=sys.stderr)
                except OSError:
                    pass
                raise
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=10)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                except OSError:
                    pass
        if port is not None:
            for _ in range(20):
                if port_closed(port):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError(f"AppImage server stayed on port {port} after shutdown")
    print(f"AppImage SHA-256 {digest}: HTTP 200, FrameControl/1, server stopped")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: check-appimage-launch.py path/to/AppImage")
    check(Path(sys.argv[1]).resolve())
