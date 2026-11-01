"""client-mini: quantclass-client 的无 UI(无头)调度复刻版。

仅复现调度闭环,对应原仓库 src/main 下的逻辑:
- scheduler.ts   -> scheduler.py    每分钟主调度 + 每 5 分钟 min_data 调度
- process.ts     -> launcher.py     execBin: 权限检查 + spawn 内核 + 环境变量注入
- tools.ts       -> kernel_state.py .py.lock 状态机 + 强杀
- server/*       -> server.py       /heartbeat /error /notify /toast 回调服务
- heartbeat.ts   -> heartbeat.py    心跳监控 + 机器人告警
- tokenStore.ts  -> auth.py         refresh_token -> access_token
- userStore.ts   -> user.py         /user/info/v3 用户信息与权限

对正式客户端的文件完全只读:real_market_25.json(由正式客户端生成)、
server_port(读值精确绑定回调端口,不写回、不自选)。
"""

__version__ = "0.1.0"
