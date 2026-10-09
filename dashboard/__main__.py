"""python -m dashboard：启动看板，并在它退出后自动重新启动。

看板服务（dashboard/server.py）在两种情况下会退出：自己的代码被 git pull 更新了（要用新代码重启），
或者意外出错。这一层只负责把它重新拉起来，本身几乎不会改动。已经有一个看板在运行时直接退出。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

from .server import APP_ID, EXIT_CODE_CHANGED, EXIT_PORT_BUSY, ROOT

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def port_from(argv: list[str]) -> int:
    if "--port" in argv:
        return int(argv[argv.index("--port") + 1])
    from . import data
    return data.settings(data.load_config(ROOT))["port"]


def running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
            return json.loads(r.read()).get("app") == APP_ID
    except (OSError, ValueError):
        return False


def main(argv: list[str]) -> int:
    port = port_from(argv)
    if running(port):
        print(f"看板已经在运行：http://localhost:{port}")
        return 0
    delay = 5
    while True:
        started = time.monotonic()
        child = subprocess.Popen([sys.executable, "-m", "dashboard.server", *argv], cwd=str(ROOT),
                                 creationflags=NO_WINDOW)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            return 0
        if code == EXIT_CODE_CHANGED:
            delay = 5
            continue
        if code == 0:
            return 0
        if code == EXIT_PORT_BUSY and running(port):
            return 0
        if time.monotonic() - started > 600:
            delay = 5
        time.sleep(delay)
        delay = min(delay * 2, 300)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
