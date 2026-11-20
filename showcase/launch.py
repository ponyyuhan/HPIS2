"""Open the local presentation, starting its services if needed."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from .server import cache_default


def running(url):
    try:
        with urllib.request.urlopen(url + "/api/bootstrap", timeout=2) as response:
            value = json.load(response)
        return isinstance(value.get("dataset"), dict) and "presets" in value
    except (OSError, ValueError, urllib.error.URLError):
        return False


def main():
    parser = argparse.ArgumentParser(description="打开 HPIS 2.0 本地演示")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not running(url):
        cache = cache_default()
        cache.mkdir(parents=True, exist_ok=True)
        log = cache / "presentation-server.log"
        with log.open("ab") as stream:
            process = subprocess.Popen(
                [sys.executable, "-m", "showcase", "--port", str(args.port)],
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=stream,
                start_new_session=True,
                env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            )
        for _ in range(50):
            if running(url):
                (cache / f"presentation-{args.port}.pid").write_text(str(process.pid))
                break
            if process.poll() is not None:
                raise SystemExit(f"服务启动失败。请查看 {log}")
            time.sleep(0.2)
        else:
            raise SystemExit(f"服务正在初始化。请查看 {log}")
    if not args.no_browser:
        webbrowser.open(url)
    print(url)


if __name__ == "__main__":
    main()
