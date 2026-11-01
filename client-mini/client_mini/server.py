"""本地回调服务,对应 src/main/server/index.ts(Hono)。

路由:POST /heartbeat、/error、/notify、/toast;GET /product-status

端口策略(mini 完全只读):绑定正式 config.json 里已记录的 server_port
(rocket 启动后就是往这个端口回调)。端口被占用直接报错——不自动 +1,
因为换端口 rocket 就找不回来了;被占用通常说明正式客户端还在运行。
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import log

logger = log.get()

DEFAULT_PORT = 8787


class _Handler(BaseHTTPRequestHandler):
    # 类属性,由 serve() 注入
    state = None
    token_store = None

    def log_message(self, fmt, *args):  # 静默默认访问日志,走统一 logger
        logger.info(f"[server] {self.address_string()} {fmt % args}")

    # ------------------------------------------------------------------
    def _json(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ------------------------------------------------------------------
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/product-status"):
            self._json({"code": 200, "message": "success", "data": {"status": "ok"}})
            return
        self._json({"code": 404, "message": "not found"}, 404)

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]

        if path == "/heartbeat":
            import time

            now_ms = int(time.time() * 1000)
            self.state.set("last_heartbeat_timestamp", now_ms)
            logger.info("[rocket] 心跳检测成功,更新时间戳")
            self._json({"code": 200, "message": "success",
                        "data": {"timestamp": now_ms}})
            return

        if path == "/error":
            logger.error(f"[server] 内核错误上报: {json.dumps(body, ensure_ascii=False)}")
            self._json({"code": 200, "message": "success"})
            return

        if path == "/notify":
            logger.info(f"[server] 内核通知: {json.dumps(body, ensure_ascii=False)}")
            self._json({"code": 200, "message": "success"})
            return

        if path == "/toast":
            logger.info(f"[server] toast: {json.dumps(body, ensure_ascii=False)}")
            self._json({"code": 200, "message": "success"})
            return

        self._json({"code": 404, "message": "not found"}, 404)


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def serve(state, token_store=None, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """精确绑定指定端口(即 config 里的 server_port),占用即抛错。"""
    if not _is_port_available(port):
        raise RuntimeError(
            f"端口 {port} 被占用——rocket 按 config.json 的 server_port 回调,"
            f"mini 不能换端口;请确认正式客户端已退出(或残留进程未杀)"
        )

    handler = type(f"MiniHandler", (_Handler,), {"state": state,
                                                 "token_store": token_store})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name=f"http-{port}").start()
    logger.info(f"服务器正在监听 http://localhost:{port}")
    return httpd
