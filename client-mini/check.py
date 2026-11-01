"""一键预检:在虚拟机里跑这个,逐项验证调度链路是否就绪。

    py check.py --config config.json

检查项(全部通过才建议无人值守):
    [ok/fail] 配置加载(attach/standalone)
    [ok/fail] refresh_token(手填或 DPAPI 自动解密)
    [ok/fail] token 刷新(POST /user/auth/refresh)
    [ok/fail] 用户信息 + isMember/isStock 权限
    [ok/fail] 实盘条件链配置(qmt_mode/account_id/qmt_connect_ok/auto_real_trading)
    [ok/fail] rocket 内核二进制存在
    [warn ]  QMT 进程在跑(检测常见进程名,不算硬失败)
    [warn ]  period_offset.csv 交易日历存在
    [warn ]  real_market_25.json 存在(mini 只读,由正式客户端生成)
    [ok/fail] server_port 端口可绑定(须与 config 一致,不能 +1)
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from client_mini import log  # noqa: E402
from client_mini.config import IS_WINDOWS, MiniConfig, StateStore  # noqa: E402
from client_mini.auth import TokenStore  # noqa: E402
from client_mini.user import UserStore  # noqa: E402
from client_mini.server import _is_port_available  # noqa: E402

RESULTS: list[tuple[str, str]] = []  # (status, message)


def record(status: str, message: str):
    RESULTS.append((status, message))
    icon = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[fail]"}[status]
    print(f"  {icon} {message}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    args = parser.parse_args()
    log.setup(tempfile.mkdtemp(), level=logging_level())
    logger = log.get()

    print(f"== client-mini 预检({args.config}) ==")

    # 1. 配置加载 ---------------------------------------------------------
    try:
        cfg = MiniConfig(args.config)
        mode = "attach" if cfg.bootstrap.get("client_config") else "standalone"
        record("ok", f"配置加载({mode} 模式),数据目录 {cfg.data_dir}")
    except Exception as e:
        record("fail", f"配置加载失败: {e}")
        return summary()

    # 2. refresh_token ----------------------------------------------------
    tokens = TokenStore(cfg.base_url, cfg.refresh_token)
    if tokens.has_refresh_token:
        record("ok", "refresh_token 就绪" + ("" if cfg.refresh_token else "(DPAPI 自动解密)"))
    else:
        record("fail", "无 refresh_token(手填 auth.refresh_token 或确认 .enc 可解密)")
        return summary()

    # 3. token 刷新 -------------------------------------------------------
    access = tokens.get_access_token(force=True)
    record("ok" if access else "fail",
           "token 刷新成功" if access else "token 刷新失败(检查网络/refresh_token 有效性)")

    # 4. 用户信息与权限 ---------------------------------------------------
    state = StateStore(cfg.state_dir)
    user_store = UserStore(cfg.base_url, tokens, state)
    user = user_store.get_user_account(force=True)
    if not user or not user.get("is_logged_in"):
        record("fail", "用户信息获取失败")
    else:
        perms = user.get("permissions") or []
        has_perm = "isMember" in perms and "isStock" in perms
        record("ok" if has_perm else "fail",
               f"用户已登录,权限 {perms}" + ("" if has_perm else "(缺 isMember/isStock,无法自动下单)"))

    # 5. 实盘条件链配置 ---------------------------------------------------
    rmc = cfg.real_market_config
    checks = {
        "qmt_mode=planA": rmc.get("qmt_mode") == "planA",
        "account_id 非空且非0": str(rmc.get("account_id", "0")) != "0",
        "qmt_connect_ok": cfg.data.get("qmt_connect_ok") is True,
        "auto_real_trading 开": cfg.get_switch("auto_real_trading") is True,
    }
    for name, passed in checks.items():
        record("ok" if passed else "warn", f"条件链-{name}" + ("" if passed else " 不满足"))

    # 6. rocket 二进制 ----------------------------------------------------
    rocket_bin = cfg.get_code_data_path("rocket") / ("rocket.exe" if IS_WINDOWS else "rocket")
    record("ok" if rocket_bin.exists() else "fail",
           f"rocket 内核 {rocket_bin}" + ("" if rocket_bin.exists() else " 不存在(手动放置)"))

    # 7. QMT 进程(尽力检测) ----------------------------------------------
    qmt_names = ["XtItClient", "xtquant", "QMT", "XtMiniQmt"]
    qmt_running = False
    if IS_WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/NH"], capture_output=True,
                                 text=True, timeout=15).stdout
            qmt_running = any(n.lower() in out.lower() for n in qmt_names)
        except Exception as e:
            logger.info(f"[check] tasklist 失败: {e}")
    record("ok" if qmt_running else "warn",
           "QMT 进程在运行" if qmt_running else "未检测到 QMT 进程(确认已启动并登录)")

    # 8. 交易日历 ----------------------------------------------------------
    csv_path = cfg.data_dir / "period_offset.csv"
    record("warn" if not csv_path.exists() else "ok",
           f"交易日历 {csv_path}" + ("" if csv_path.exists() else " 缺失(min_data/心跳检查会跳过)"))

    # 9. real_market_25.json 只读前提 --------------------------------------
    rm25 = cfg.config_dir / "real_market_25.json"
    record("warn" if not rm25.exists() else "ok",
           f"real_market_25.json {'存在' if rm25.exists() else '缺失(先跑一次正式客户端生成,mini 只读不写)'}")

    # 10. 回调端口:必须精确等于 config 的 server_port(mini 只读,不能 +1) ----
    port = int(cfg.data.get("server_port", 8787))
    available = _is_port_available(port)
    record("ok" if available else "fail",
           f"server_port={port} 可绑定" if available
           else f"端口 {port} 被占用(rocket 按 config 的此端口回调,mini 不能换端口;正式客户端还在跑?)")

    return summary()


def logging_level():
    import logging
    return logging.WARNING  # 预检时压掉 info 日志,只看检查结果


def summary() -> int:
    fails = [m for s, m in RESULTS if s == "fail"]
    warns = [m for s, m in RESULTS if s == "warn"]
    print(f"\n== 结果: {len(RESULTS)} 项,{len(fails)} 失败,{len(warns)} 警告 ==")
    if fails:
        print("存在硬性失败,修复后再无人值守:")
        for m in fails:
            print(f"  - {m}")
        return 1
    print("链路就绪,可以启动: py run.py --config config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
