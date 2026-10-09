"""看板的本地网页服务。一般不直接运行它，而是运行 python -m dashboard（出错或代码更新后会自动重启）。

    python -m dashboard.server            正常运行，连本机 OpenD
    python -m dashboard.server --demo     演示模式：不连 OpenD，行情是模拟的

只监听 127.0.0.1，只有 GET 请求，没有任何能改文件、下单的接口。
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import mimetypes
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from . import data, gitsync, quotes

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
APP_ID = "moomoo-dashboard"
EXIT_CODE_CHANGED = 3
EXIT_PORT_BUSY = 2

log = logging.getLogger("dashboard")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".ico": "image/x-icon", ".json": "application/json; charset=utf-8"}


def clean(obj):
    """JSON 里不能有 NaN / Infinity。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


class App:
    def __init__(self, root: Path, feed: quotes.Feed, git: gitsync.GitSignals | None):
        self.root, self.feed, self.git = root, feed, git
        self.files = data.Files(root)
        self._lock = threading.Lock()

    def dashboard(self) -> dict:
        with self._lock:  # 文件缓存不是线程安全的
            return clean(data.assemble(self.root, self.files, self.feed.snapshot(),
                                       self.git.snapshot() if self.git else {}, self.feed.now()))


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "moomoo-dashboard"

        def log_message(self, fmt, *args):  # 不往控制台刷每个请求
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/dashboard":
                    return self._json(200, app.dashboard())
                if path == "/api/health":
                    return self._json(200, {"app": APP_ID, "ok": True})
                if path in ("/", "/index.html"):
                    return self._file(STATIC / "index.html")
                if path.startswith("/static/"):
                    return self._file(STATIC / path[len("/static/"):])
                return self._json(404, {"error": "not found"})
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as e:  # noqa: BLE001
                log.exception("处理 %s 出错", path)
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})

        def _file(self, p: Path):
            p = p.resolve()
            if STATIC not in p.parents or not p.is_file():
                return self._json(404, {"error": "not found"})
            # Windows 的注册表可能把 .js 登记成 text/plain，浏览器会拒绝加载，所以常见类型写死
            ctype = TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return self._send(200, p.read_bytes(), ctype)

        def do_POST(self):  # noqa: N802
            self._json(405, {"error": "看板是只读的"})

        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


def code_stamp() -> dict:
    return {str(p): p.stat().st_mtime for p in HERE.glob("*.py")}


def watch_code(server: ThreadingHTTPServer, flag: dict):
    """看板自己的 Python 代码更新了（交易程序 git pull 拉下来的），退出让外层重新启动，新代码就生效了。
    网页文件每次请求都重新读，不需要重启。"""
    start = code_stamp()
    while True:
        time.sleep(30)
        if code_stamp() != start:
            log.info("看板代码有更新，重启")
            flag["code"] = EXIT_CODE_CHANGED
            server.shutdown()
            return


def setup_logging(root: Path, log_dir: str):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.setLevel(logging.INFO)
    try:
        (root / log_dir).mkdir(exist_ok=True)
        fh = RotatingFileHandler(root / log_dir / "dashboard.log", maxBytes=1_000_000, backupCount=2,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


def build_feed(root: Path, cfg: dict, st: dict, demo: bool) -> quotes.Feed:
    files = data.Files(root)
    records = files.run_records(cfg.get("log_dir", "logs"))
    sig = cfg.get("signal") or {}
    codes = [c for c, _ in st["market"]]
    codes += [c for cs in (sig.get("groups") or data.DEFAULT_GROUPS).values() for c in cs]
    codes += list((cfg.get("targets") or {}).keys()) + list((cfg.get("baseline") or {}).get("gold_codes") or [])
    for r in records[-5:]:
        codes += list((r.get("holdings") or {}).keys())
    codes = list(dict.fromkeys(codes))
    if demo:
        day = records[-1]["time"][:10] if records else "2026-10-08"
        closes = (files.json("data/status.json") or {}).get("daily_closes") or {}
        prev = {c: [p for d, p in v if d < day][-1] for c, v in closes.items() if any(d < day for d, _ in v)}
        rows = files.csv_rows(f"{cfg.get('log_dir', 'logs')}/strategies.csv")
        rows = [r for r in rows if r["time"][:10] < day and r.get("fixed_100")]
        # 演示用的大盘历史：跟着 100% 股票对照线走，最后一天对上模拟行情的昨收
        spy_daily = [(r["time"][:10], round(668.4 * float(r["fixed_100"]) / float(rows[-1]["fixed_100"]), 2))
                     for r in rows] if rows else []
        return quotes.DemoFeed(prev, codes, day, spy_daily)
    try:
        import moomoo  # noqa: F401
    except ImportError:
        return quotes.OfflineFeed("没有安装 moomoo-api，只显示程序运行时记下的数据")
    feed = quotes.OpenDFeed(cfg.get("opend_host", "127.0.0.1"), int(cfg.get("opend_port", 11111)), codes,
                            data.BENCH, str(cfg.get("start_date", "")))
    return feed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dashboard.server")
    p.add_argument("--demo", action="store_true", help="演示模式：不连 OpenD，行情是模拟的")
    p.add_argument("--port", type=int, help="端口（默认读 config.yaml 的 dashboard.port，没有就是 8080）")
    p.add_argument("--root", default=str(ROOT), help="程序文件夹")
    p.add_argument("--no-git", action="store_true", help="不定时 git fetch")
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    cfg = data.load_config(root)
    st = data.settings(cfg)
    port = args.port or st["port"]
    setup_logging(root, cfg.get("log_dir", "logs"))

    feed = build_feed(root, cfg, st, args.demo)
    git = None if args.demo or args.no_git else gitsync.GitSignals(root, st["schedule"], st["git_fetch_minutes"])
    app = App(root, feed, git)
    try:
        server = ThreadingHTTPServer((st["host"], port), make_handler(app))
    except OSError as e:
        log.error("端口 %s 被占用或无法监听：%s", port, e)
        return EXIT_PORT_BUSY
    server.daemon_threads = True
    feed.start()
    if git:
        git.start()
    flag = {"code": 0}
    threading.Thread(target=watch_code, args=(server, flag), name="watch", daemon=True).start()
    log.info("看板已启动：http://localhost:%s%s", port, "（演示模式）" if args.demo else "")
    try:
        server.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        feed.stop()
        if git:
            git.stop()
    return flag["code"]


if __name__ == "__main__":
    sys.exit(main())
