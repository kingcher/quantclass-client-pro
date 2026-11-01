"""client-mini 入口。

用法:
    python run.py --config config.json

启动顺序(对应 src/main/index.ts 的 app.whenReady):
    0. 从默认内核目录同步版本不同的内核
    1. 回调服务(绑定 config.json 的 server_port,只读不写)
    2. 心跳监控线程
    3. 调度器主循环(从 config 读 auto_real_trading 等开关,恢复状态)
    4. 退出时强杀所有内核(对应 before-quit 的 killAllKernalByForce)
"""

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from client_mini import log
from client_mini.auth import TokenStore
from client_mini.config import MiniConfig, StateStore
from client_mini.heartbeat import HeartbeatMonitor
from client_mini.kernel_state import kill_all_kernels
from client_mini.kernal_copy import copy_kernels
from client_mini.launcher import process_manager
from client_mini.scheduler import Scheduler
from client_mini.server import serve
from client_mini.user import UserStore


def main():
    parser = argparse.ArgumentParser(description="quantclass client-mini(无头调度版)")
    parser.add_argument(
        "--config", default=str(Path(__file__).parent / "config.json"),
        help="配置文件路径(默认同目录 config.json)",
    )
    args = parser.parse_args()

    cfg = MiniConfig(args.config)
    log.setup(cfg.state_dir / "logs")

    logger = log.get()
    logger.info("=" * 60)
    logger.info(f"client-mini 启动,配置: {cfg.path}")
    logger.info(f"数据目录: {cfg.data_dir} | 内核目录: {cfg.code_dir}")

    try:
        copy_kernels(cfg)
    except (OSError, ValueError, RuntimeError) as e:
        logger.error(f"[boot] 内核同步失败: {e}")
        sys.exit(1)

    # -- real_market_25.json 只读前提:由正式客户端生成,mini 不写
    rm25 = cfg.config_dir / "real_market_25.json"
    if not rm25.exists():
        logger.error(f"[boot] 缺少 {rm25}")
        logger.error("[boot] 请先运行正式客户端(登录并开启自动任务)至少一分钟,")
        logger.error("[boot] 让它生成该文件后再启动 mini,否则 rocket 将读到空策略配置")

    state = StateStore(cfg.state_dir)
    tokens = TokenStore(cfg.base_url, cfg.refresh_token)
    if not tokens.has_refresh_token:
        logger.error("[boot] 缺少 auth.refresh_token,请先在 config.json 中配置")
        sys.exit(1)

    user_store = UserStore(cfg.base_url, tokens, state)

    # -- 1. 回调服务:绑定正式 config 记录的 server_port(rocket 按它回调,只读不写)
    port = int(cfg.data.get("server_port", 8787))
    try:
        httpd = serve(state, tokens, port)
    except RuntimeError as e:
        logger.error(f"[boot] {e}")
        sys.exit(1)

    # -- 2. 心跳监控
    heartbeat = HeartbeatMonitor(cfg, state, port)
    heartbeat.start()

    # -- 3. 调度器:从 config 恢复开关(对应 renderer IPC 的 set 调用)
    scheduler = Scheduler(cfg, state, tokens, user_store)
    scheduler.set_auto_update(cfg.get_switch("auto_update"))
    scheduler.set_auto_min_data(
        cfg.get_switch("auto_min_data"),
        cfg.bootstrap.get("min_data_mode", cfg.data.get("min_data_mode", "fast")),
    )
    scheduler.set_auto_trading(cfg.get_switch("auto_real_trading"))

    # -- 初始化:强制刷新一次用户信息并校验权限(对应 initializeSystem)
    user = user_store.get_user_account(force=True)
    if not user or not user.get("is_logged_in"):
        logger.warning("[boot] 用户信息获取失败,调度仍会每轮重试")

    stop_event = {"stopped": False}

    def shutdown(signum, frame):
        if stop_event["stopped"]:
            return
        stop_event["stopped"] = True
        logger.info(f"[boot] 收到信号 {signum},退出前清理内核进程")
        scheduler.stop()
        heartbeat.stop()
        kill_all_kernels(cfg)
        httpd.shutdown()
        logger.info("[boot] 已退出")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info(f"[boot] 当前进程: {process_manager.get_running_processes()}")
    try:
        scheduler.run_forever()
    finally:
        if not stop_event["stopped"]:
            shutdown(signal.SIGTERM, None)


if __name__ == "__main__":
    main()
