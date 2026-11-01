"""调度器,完整移植 src/main/lib/scheduler.ts。

- 主 job:每分钟触发(对齐分钟边界),顺序:
  网络检查 -> 登录检查 -> writeRealMarket25 -> wakeUpRocket ->
  内核忙检查 -> fuel 数据更新(15min 时间表) -> 选股(15min 时间表)
- min_data job:每 5 分钟(第 1、6、11...分),窗口内取 QMT 准确数据
"""

import threading
import time
from datetime import datetime

from . import log
from .config import MiniConfig
from .kernel_state import (
    is_any_kernel_busy,
    is_kernel_busy,
    is_kernel_running,
    kill_kernel_by_force,
)
from .launcher import exec_bin, process_manager
from .permission import check_permission, get_select_kernel
from .trading_time import (
    get_local_ymd,
    is_min_data_intraday_window,
    is_trading_time,
    load_trading_days,
)

logger = log.get()


class SystemState:
    """对应 scheduler.ts 的 systemState(内存态,不持久化)。"""

    def __init__(self):
        self.is_set_auto_update = False
        self.is_set_auto_trading = False
        self.is_set_auto_min_data = False
        self.min_data_mode = "fast"  # fast | stable
        self.is_online = True

    def dump(self) -> str:
        return (
            f"自动数据: {self.is_set_auto_update}, "
            f"自动下单: {self.is_set_auto_trading}, 在线: {self.is_online}"
        )


class Scheduler:
    def __init__(self, cfg: MiniConfig, state, tokens, user_store):
        self._cfg = cfg
        self._state_store = state
        self._tokens = tokens
        self._user_store = user_store
        self.system_state = SystemState()
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    # ==================================================================
    # 主循环:每秒 tick,分钟变化触发主 job,每 5 分钟触发 min_data
    # ==================================================================
    def run_forever(self):
        logger.info("[scheduler] 调度器启动")
        last_minute = -1
        last_min_data_minute = -1
        while not self._stop.is_set():
            now = datetime.now()
            if now.minute != last_minute:
                last_minute = now.minute
                try:
                    self._main_tick()
                except Exception as e:
                    logger.error(f"[scheduler] 主调度异常: {e}")

            # node-schedule "1/5 * * * *" => 第 1、6、11...分触发
            if (now.minute - 1) % 5 == 0 and now.minute != last_min_data_minute:
                last_min_data_minute = now.minute
                try:
                    self._min_data_tick()
                except Exception as e:
                    logger.error(f"[min-data] 调度异常: {e}")

            self._stop.wait(1)

    # ==================================================================
    # 主 job(对应 setupScheduler 里的每分钟闭包)
    # ==================================================================
    def _main_tick(self):
        logger.info(">>>>>>>>>>>>>>>> scheduler start <<<<<<<<<<<<<<<<")
        ss = self.system_state
        user_account = self._user_store.get_user_account() or {}

        # 对应正式客户端:setupScheduler 由 setAutoUpdate(true) 创建,
        # 全关时每分钟 job 根本不存在(is_set_auto_trading 会强制带动 update)
        if not ss.is_set_auto_update and not ss.is_set_auto_trading:
            return

        if not ss.is_online:
            logger.info("[network] 网络离线,跳过本轮调度")
            return

        if not user_account.get("is_logged_in"):
            logger.info("[user] 用户未登录,跳过本轮调度")
            return

        # real_market_25.json 由正式客户端生成,mini 只读不写(配置视为静态)
        logger.info(f"[scheduler] {ss.dump()}, 用户登录: {user_account.get('is_logged_in')}")

        require_trading = ss.is_set_auto_trading

        if require_trading:
            self._wake_up_rocket(user_account)

        select_kernel = get_select_kernel(user_account.get("permissions"))
        busy_kernels = [select_kernel] if is_trading_time() else [select_kernel, "fuel"]
        if is_any_kernel_busy(self._cfg, busy_kernels):
            logger.info("[scheduler] 内核正忙,跳过本轮调度")
            return

        # -- fuel 数据更新
        if ss.is_set_auto_update:
            data_module_times = self._cfg.schedule.get("dataModule", []) or []
            data_module_auto = len(data_module_times) == 0
            current_15m = self._current_15m()
            is_schedule_data_module = (
                data_module_auto or current_15m in data_module_times
            )
            logger.info(
                f"[scheduler-fuel] 数据模块定时任务: {data_module_times}, "
                f"当前时间: {current_15m}, 是否更新: {is_schedule_data_module}"
            )
            is_fuel_busy = is_kernel_busy(self._cfg, "fuel")
            allow_concurrent = is_trading_time()
            if is_fuel_busy and not allow_concurrent:
                logger.info("[fuel] 内核正忙,跳过本轮调度")
            elif not is_schedule_data_module:
                logger.info("[fuel] 非定时更新数据时间,跳过本轮数据更新")
            else:
                self._wake_up_fuel()

        # -- 选股
        if require_trading:
            select_module_times = self._cfg.schedule.get("selectModule", []) or []
            select_module_auto = len(select_module_times) == 0
            current_15m = self._current_15m()
            is_schedule_select_module = (
                select_module_auto or current_15m in select_module_times
            )
            logger.info(
                f"[scheduler-select] 选股模块定时任务: {select_module_times}, "
                f"当前时间: {current_15m}, 是否选股: {is_schedule_select_module}"
            )
            if is_kernel_busy(self._cfg, select_kernel):
                logger.info(f"[{select_kernel}] 内核正忙,跳过本轮调度")
            elif not is_schedule_select_module:
                logger.info(f"[{select_kernel}] 非定时选股时间,跳过本轮选股")
            else:
                self._wake_up_fusion(user_account)
        else:
            logger.info("[scheduler] 未启用自动实盘,跳过选股")

        logger.info(">>>>>>>>>>>>>>>> scheduler done <<<<<<<<<<<<<<<<")

    @staticmethod
    def _current_15m() -> str:
        now = datetime.now()
        return f"{now.hour:02d}:{now.minute // 15 * 15:02d}"

    # ==================================================================
    # wakeUp*(对应同名函数)
    # ==================================================================
    def _wake_up_fuel(self):
        logger.info("[fuel] 自动更新所有数据...")
        exec_bin(self._cfg, self._tokens, {}, ["all_data"], "自动更新所有数据")

    def _wake_up_fusion(self, user_account: dict):
        permissions = user_account.get("permissions") or []
        if not check_permission(permissions, "isMember", "isStock"):
            logger.info(
                f"[select] 无分享会或股票权限,跳过选股,{user_account.get('user', {}).get('nickname', '')}"
            )
            return
        select_kernel = get_select_kernel(permissions)
        try:
            exec_bin(self._cfg, self._tokens, user_account,
                    ["select", "trading"], "选股", select_kernel)
        except Exception as e:
            logger.info(f"[{select_kernel}] runtime error({e})")

    def _wake_up_rocket(self, user_account: dict):
        rmc = self._cfg.real_market_config
        qmt_mode = rmc.get("qmt_mode", "planX")
        qmt_connect_ok = self._cfg.data.get("qmt_connect_ok", False) is True
        if qmt_mode != "planA" or not qmt_connect_ok:
            return

        if rmc.get("account_id", "0") == "0":
            logger.info("[rocket] QMT账号为0,激活手动下单模式,不启动Rocket")
            if is_kernel_busy(self._cfg, "rocket"):
                logger.info("[rocket] Rocket正在运行,强制杀死")
                kill_kernel_by_force(self._cfg, "rocket", strict=True)
            return

        if not check_permission(user_account.get("permissions"), "isMember", "isStock"):
            logger.info("[trade] 无分享会或股票权限,跳过Rocket")
            return

        if not is_trading_time():
            logger.info("[rocket] 非交易日,不唤醒Rocket")
            return

        logger.info("[rocket] 交易时间,确认 rocket 状态")
        self._start_rocket_if_needed()

    def _start_rocket_if_needed(self):
        if is_kernel_busy(self._cfg, "rocket"):
            logger.warning("[rocket] 已在运行中,跳过操作")
            return
        user_account = self._user_store.get_user_account() or {}
        exec_bin(self._cfg, self._tokens, user_account,
                 ["run"], "启动 rocket", "rocket")

    # ==================================================================
    # min_data job(对应 wakeUpMinData)
    # ==================================================================
    def _min_data_tick(self):
        # 对应 setAutoMinData: 开关关闭时 min_data 定时任务不存在
        if not self.system_state.is_set_auto_min_data:
            return
        if not is_min_data_intraday_window():
            logger.info("[min-data] 非交易时段,跳过本轮")
            return

        calendar = load_trading_days(self._cfg.data_dir)
        if not calendar:
            logger.info("[min-data] 未读到 period_offset.csv 交易日历,跳过本轮")
            return
        if get_local_ymd() not in calendar:
            logger.info(f"[min-data] 今日 {get_local_ymd()} 非交易日,跳过本轮")
            return

        ss = self.system_state
        user_account = self._user_store.get_user_account() or {}

        if not ss.is_online:
            logger.info("[min-data] 网络离线,跳过本轮")
            return
        if not user_account.get("is_logged_in"):
            logger.info("[min-data] 用户未登录,跳过本轮")
            return
        if is_kernel_running(self._cfg, "fuel") and not is_trading_time():
            logger.info("[min-data] Fuel 内核正忙,跳过本轮")
            return

        args = ["min_data", "thread"] if ss.min_data_mode == "stable" else ["min_data"]
        action = (
            f"定时获取准确QMT数据-{'稳定' if ss.min_data_mode == 'stable' else '极速'}模式"
        )
        try:
            exec_bin(self._cfg, self._tokens, user_account, args, action)
            logger.info("[min-data] 准确数据获取完成")
        except Exception as e:
            logger.error(f"[min-data] 准确数据获取失败: {e}")

    # ==================================================================
    # 开关(对应 setAutoUpdate / setAutoTrading / setAutoMinData)
    # ==================================================================
    def set_auto_update(self, is_on: bool):
        self.system_state.is_set_auto_update = is_on
        logger.info(f"[fuel] 自动更新数据: {is_on}")

    def set_auto_trading(self, is_on: bool):
        self.system_state.is_set_auto_trading = is_on
        logger.info(f"[trade] 自动交易: {is_on}")
        if is_on and not self.system_state.is_set_auto_update:
            self.set_auto_update(True)  # 启动实盘则自动更新也要开启
        # 开关视为静态:启动时从 config 读入,运行期不回写

    def set_auto_min_data(self, is_on: bool, mode: str | None = None):
        self.system_state.is_set_auto_min_data = is_on
        if mode is not None:
            self.system_state.min_data_mode = mode
        logger.info(
            f"[min-data] 自动更新: {is_on}, 模式: {self.system_state.min_data_mode}"
        )
