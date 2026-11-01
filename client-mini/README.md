# client-mini

`quantclass-client-pro` 的**无 UI 调度复刻版**(纯 Python 标准库,≥3.10,零第三方依赖)。
只复现调度闭环:定时唤醒 rocket/fuel/选股内核 + 心跳监控 + 告警,不含界面、
内核下载、自动更新等客户端能力。

## 模块对照

| mini 模块 | 原仓库文件 | 职责 |
|---|---|---|
| `scheduler.py` | `src/main/lib/scheduler.ts` | 每分钟主调度 + 每 5 分钟 min_data 调度,全部条件链 |
| `launcher.py` | `src/main/lib/process.ts` | `execBin`:权限检查 → spawn 内核 → 环境变量注入 → stdout/stderr 日志 |
| `kernel_state.py` | `src/main/utils/tools.ts` | `.py.lock` 状态机、更新锁、`isKernalBusy`、强杀 |
| `server.py` | `src/main/server/index.ts` | `/heartbeat` `/error` `/notify` `/toast` 回调服务,绑定 config 的 `server_port`(只读不改) |
| `heartbeat.py` | `src/main/server/heartbeat.ts` | 交易时段每 30s 检查心跳,超 2 分钟机器人告警(重试 3 次) |
| `auth.py` | `src/main/lib/tokenStore.ts` | refresh_token → access_token,20min 临近异步刷新,并发去重 |
| `user.py` | `src/main/lib/userStore.ts` + `utils/user.ts` | `/user/info/v3` 用户信息,缓存 15h+随机 4h,权限计算 |
| `trading_time.py` | `tools.ts` + `shared/lib/trading-day.ts` | 交易时段、min_data 窗口、period_offset.csv 交易日历 |
| `config.py` | `src/main/store/index.ts` | 配置/路径/运行时状态(electron-store 的无头替代) |

## 快速开始

### 方式一:attach 模式(推荐,配置最少)

只用正式客户端的 config.json,mini 不重复维护任何业务配置:

```bash
cp config.attach.example.json config.json
# 里面只需要三样:
#   client_config  -> 正式客户端 config.json 的地址
#   auth.refresh_token -> 登录凭证;留空则尝试自动解密同目录 refresh-token.enc
#                      (仅当 mini 与正式客户端跑在同一 Windows 用户下可解出;
#                       macOS/Linux 的 .enc 是 Keychain+AES 格式,解不了,必须手填)
#   state_dir      -> mini 自己的运行时状态目录
python run.py --config config.json
```

attach 模式下 mini 的取值规则(与正式客户端完全一致):

| mini 需要 | 来源 |
|---|---|
| `data_dir` | 正式 config 的 `settings.all_data_path` |
| `code_dir` | `CODE_DATA_PATH` 环境变量,缺省则 `{all_data_path}/code` |
| 换仓时间 | 正式客户端同目录 `rebalance_time.json` |
| `real_market_config` / `schedule` / `pos_mgmt.strategies` / 开关 | 直接读正式 config |
| `real_market_25.json` | **只读不写**:由正式客户端生成(其调度器每分钟写),mini 假定配置静态;rocket 按 `FUEL_CLIENT_CONFIG_PATH` 同目录找到它 |
| `FUEL_CLIENT_CONFIG_PATH` | 即正式 config.json 本身;rocket 从它读 `server_port`,mini 也读同一个值来绑定回调端口,**不写回** |

正式客户端 config.json 的位置:
- Windows:`%APPDATA%\QuantclassClient\config.json`
- macOS:`~/Library/Application Support/QuantclassClient/config.json`

**mini 对正式客户端的文件完全只读**(config.json、rebalance_time.json、
real_market_25.json、refresh-token.enc 一个都不写)。

**约束:mini 与正式客户端不能同时运行**——mini 按固定端口绑定
(config 里的 server_port),正式客户端启动也会绑同一个端口,互相顶掉后
rocket 的回调就不知道落到谁头上。改策略配置的流程:
停 mini → 开正式客户端改好并让其跑一轮 → 关正式客户端 → 起 mini。

### 方式二:standalone 模式

全部配置自己维护(不依赖正式客户端):把 `data_dir`、`code_dir`、
`state_dir`、`auth.refresh_token`、`real_market_config`、`pos_mgmt.strategies`
等键直接写进 mini 的 config.json(键名与正式客户端一致)。注意此模式下
`real_market_25.json` 没有生成方,需自行提供。

## 配置说明(standalone 模式)

| 键 | 说明 |
|---|---|
| `data_dir` | 数据根目录(等价 `settings.all_data_path`),`real_trading/`、`period_offset.csv` 在其下 |
| `code_dir` | 内核目录(等价 `CODE_DATA_PATH`),`{kernel}/{kernel}.exe` 放这里,**mini 不自动下载内核** |
| `state_dir` | 运行时状态目录(等价 userData):`state.json`、更新锁、日志 |
| `auth.refresh_token` | 登录凭证。正式客户端里由 UI 登录写入 `refresh-token.enc`,mini 直接明文配置 |
| `settings.api_key` / `hid` | 调度层游客判断用 |
| `server_port` | 回调服务端口;mini 精确绑定此端口(占用即报错退出),不写回、不自选;rocket 通过 `FUEL_CLIENT_CONFIG_PATH`(即本 config.json)读到它 |
| `auto_real_trading` | 自动实盘总开关(开启时自动带动 `auto_update`) |
| `qmt_connect_ok` | QMT 连通标志。正式客户端由 startup-check 探测写入,mini 需自行确认后手动置 true |
| `real_market_config.*` | `qmt_mode` 必须为 `planA` 才自动下单;`account_id` 为 `"0"` 时走手动模式并停掉 rocket |
| `schedule.dataModule/selectModule` | fuel/选股时间表(`HH:mm`,空数组 = 每个 15 分钟点都执行) |
| `rebalance_time` | 换仓时间配置,格式同正式客户端 `rebalance_time.json` |
| `pos_mgmt.strategies` | 仓管策略库,结构同正式客户端 config.json 内的同名键 |

## rocket 启动条件链(每分钟评估,全部满足才 spawn)

```
auto_real_trading ─ 网络 online ─ 用户已登录 ─ qmt_mode=planA ─ qmt_connect_ok
─ account_id≠0 ─ 权限 isMember+isStock ─ 交易时段(周一~五 9:00–15:30)
─ 内核不忙(.py.lock + tasklist) → spawn rocket run + 环境变量
```

注入的环境变量与正式客户端一致:`FUEL_CODE_PATH`、`FUEL_CLIENT_CONFIG_PATH`、
`FUEL_PRO_TRADING_PATH`、`PYTHONPATH`、`FUEL_ACCESS_TOKEN`、`FUEL_TEMP_FILE_PATH`、
`USE_OPEN_SELL`、`PYTHON8/PYTHONUNBUFFERED/PYTHONIOENCODING`。

## 与正式客户端的差异(有意为之)

1. **不自动下载/更新内核**:内核缺失时告警跳过,请手动放置到 `code_dir/{kernel}/`
2. **对正式客户端文件完全只读**:`real_market_25.json`(原客户端每分钟重编译)
   与 `server_port`(原客户端写回)都不写——回调端口改为精确绑定 config 里的
   既有值,绑定失败即报错退出,不自选端口
3. **refresh_token 明文配置**:无 UI 无法走登录流程;token 轮换只更新内存,需要时手动改配置
4. **基础版(非会员)策略分支不适用**:自动下单要求 isMember+isStock
5. **遥测(postUserMainAction/postTelemetryReport)未实现**
6. **`/heartbeat` 路由不限定 Windows**(原版仅 Windows 注册)
6. **内核目录约定**:mini 与正式客户端可共用同一 `code_dir`/`data_dir`,但请勿两边同时调度
   (attach 模式下更不要同时运行,见上)

## refresh_token 从哪来

正式客户端登录后写在 userData 的 `refresh-token.enc`,是 Electron safeStorage 密文:

- **mini 与正式客户端同一 Windows 用户运行**(如都在虚拟机内):留空
  `auth.refresh_token` 即可,mini 会经 DPAPI 自动解密(safe_storage.py)
- **mini 跑在 macOS / 其他机器**:DPAPI blob 跨机器解不开,须手动填——
  抓包正式客户端的 `POST /user/auth/refresh` 请求头 `Authorization: Bearer xxx`,
  或临时实现一次登录接口取 token 后填入配置

## 无人值守

- Windows:`nssm install client-mini "python.exe" "run.py" --config config.json`
- macOS/Linux:launchd / systemd,或直接 `nohup python run.py &`
- 退出行为:SIGINT/SIGTERM → 停调度 → 停心跳 → 强杀所有内核(对应 before-quit)

## 在 Parallels 虚拟机内部署(mini 与正式客户端同机)

这是推荐形态:rocket、QMT、正式客户端、mini 同一个 Windows 用户,DPAPI
自动解密 refresh_token,rocket 的 localhost 回调直达 mini 的回调服务。

一次性准备:

1. 虚拟机里装 Python ≥3.10(勾选 Add to PATH)
2. 正式客户端**登录一次**并完成实盘配置(策略库、qmt_mode、账号、
   startup-check 通过后 config 里才有 `qmt_connect_ok: true`);
   **开启自动任务跑至少一分钟**——让它生成 `real_market_25.json`(mini 只读它),然后**退出**
3. 拷贝 client-mini 目录进虚拟机,`copy config.attach.example.json config.json`,
   `client_config` 指向 `%APPDATA%\QuantclassClient\config.json`,
   `auth.refresh_token` 留空(DPAPI 自动解密)
4. 预检:`py check.py --config config.json` —— 全绿再继续

日常运行:

```bat
py run.py --config config.json
```

开机自启(登录触发 + QMT 需设置自动登录):

```bat
schtasks /create /tn client-mini /sc onlogon /tr ^
  "py C:\client-mini\run.py --config C:\client-mini\config.json"
```

注意:mini 运行期间不要再开正式客户端——两边都要绑定 config 里记录的
同一个回调端口,同时跑会互相顶掉,rocket 的回调就不知落到谁头上。
要用 UI 时先停 mini,改完策略后**让正式客户端跑一轮**(重新生成
real_market_25.json)再切回 mini。调度开关(`auto_min_data`、
`min_data_mode` 等)可写在 mini 自己的 config.json 里,不必改正式配置。
