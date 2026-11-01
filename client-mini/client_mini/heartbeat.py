"""心跳监控,对应 src/main/server/heartbeat.ts。

- rocket 周期 POST /heartbeat -> state.last_heartbeat_timestamp
- 交易时段内每 30s 检查一次,超过 2 分钟无心跳 -> 机器人告警(重试 3 次)
- 非交易时段自动启停检测定时器(外层每 60s 判断一次)
"""

import json
import threading
import time
import urllib.request
from datetime import datetime

from . import log
from .config import MiniConfig
from .trading_time import get_local_ymd, is_weekend, load_trading_days

logger = log.get()

HEARTBEAT_CONFIG = {
    "TIMEOUT_MINUTES": 2,
    "CHECK_INTERVAL_S": 30,
    "SCHEDULE_CHECK_INTERVAL_S": 60,
    "MAX_RETRY_COUNT": 3,
    "RETRY_DELAY_S": 5,
}


class HeartbeatMonitor(threading.Thread):
    def __init__(self, cfg: MiniConfig, state, server_port: int):
        super().__init__(daemon=True, name="heartbeat-monitor")
        self._cfg = cfg
        self._state = state
        self._server_port = server_port
        self._stop = threading.Event()
        self._checker_timer: threading.Timer | None = None

    def stop(self):
        self._stop.set()
        if self._checker_timer:
            self._checker_timer.cancel()

    # ------------------------------------------------------------------
    def run(self):
        """外层循环:每 60s 判断是否处于交易时段,启停 30s 检测定时器。"""
        self._check_once()  # 启动先执行一次,确认当前是否需要启动定时器
        while not self._stop.wait(HEARTBEAT_CONFIG["SCHEDULE_CHECK_INTERVAL_S"]):
            now = datetime.now()
            in_hours = (
                not is_weekend(now)
                and 9 * 60 < now.hour * 60 + now.minute < 15 * 60 + 30
            )
            if in_hours and self._checker_timer is None:
                self._schedule_check()
                logger.info("[heart] 到达交易时间,启动心跳检测")
            elif not in_hours and self._checker_timer is not None:
                self._cancel_check()
                logger.info("[heart] 交易时间结束,停止心跳检测")
            logger.info(f"[heart] 心跳状态: {'运行中' if self._checker_timer else '未运行'}")

    def _schedule_check(self):
        self._checker_timer = threading.Timer(
            HEARTBEAT_CONFIG["CHECK_INTERVAL_S"], self._check_loop
        )
        self._checker_timer.daemon = True
        self._checker_timer.start()

    def _check_loop(self):
        if self._stop.is_set():
            return
        self._checker_timer = None
        try:
            self._check_once()
        except Exception as e:
            logger.error(f"[heart] 心跳检查异常: {e}")
        if not self._stop.is_set():
            self._schedule_check()

    def _cancel_check(self):
        if self._checker_timer:
            self._checker_timer.cancel()
            self._checker_timer = None

    # ------------------------------------------------------------------
    def _check_once(self):
        now = datetime.now()
        calendar = load_trading_days(self._cfg.data_dir)
        if get_local_ymd(now) not in calendar:
            logger.info("[heart] 非交易日,不检测心跳")
            return

        minutes = now.hour * 60 + now.minute
        if not (9 * 60 <= minutes <= 15 * 60 + 30):
            logger.info("[heart] 非交易时间,不检测心跳")
            return

        auto_real_trading = bool(self._cfg.data.get("auto_real_trading", False))
        if not auto_real_trading:
            logger.info("[heart] 未启动自动实盘,不检测心跳")
            return

        last_ts = self._state.get("last_heartbeat_timestamp")
        if not last_ts:
            self._state.set("last_heartbeat_timestamp", int(time.time() * 1000))
            logger.info("[heart] 首次启动心跳检测,初始化时间戳")
            return

        diff_min = (time.time() * 1000 - last_ts) / 1000 / 60
        if diff_min > HEARTBEAT_CONFIG["TIMEOUT_MINUTES"]:
            logger.warning(
                f"[heart] 心跳超时 {diff_min - HEARTBEAT_CONFIG['TIMEOUT_MINUTES']:.1f} 分钟,"
                f"开始发送告警通知"
            )
            self._send_msg_to_robot()
            self._state.set("last_heartbeat_timestamp", int(time.time() * 1000))

    # ------------------------------------------------------------------
    def _send_msg_to_robot(self, retry: int = 0):
        robot_url = self._cfg.real_market_config.get("message_robot_url", "")
        error_message = (
            "检测到客户端下单模块异常挂起,请重启客户端后再观察是否恢复正常,"
            "如未恢复正常请联系助教"
        )
        if not robot_url:
            self._send_error_to_client(error_message)
            logger.warning("未配置消息机器人路径")
            return

        try:
            self._send_error_to_client(error_message)
            req = urllib.request.Request(
                robot_url,
                method="POST",
                data=json.dumps(
                    {"msgtype": "text", "text": {"content": "客户端下单模块出现问题"}}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            logger.info("告警消息发送成功")
        except Exception as e:
            logger.error(f"消息机器人请求失败,重试次数: {retry} {e}")
            if retry < HEARTBEAT_CONFIG["MAX_RETRY_COUNT"]:
                threading.Timer(
                    HEARTBEAT_CONFIG["RETRY_DELAY_S"],
                    self._send_msg_to_robot, args=(retry + 1,),
                ).start()

    def _send_error_to_client(self, message: str):
        """对应 sendErrorToClient:POST 自己的 /error 让错误可见。"""
        try:
            req = urllib.request.Request(
                f"http://localhost:{self._server_port}/error",
                method="POST",
                data=json.dumps({"code": 400, "message": message}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.error(f"[sendMsgToRobot] 客户端通信失败,本地服务可能未成功启动 {e}")
