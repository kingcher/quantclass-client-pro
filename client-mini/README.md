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

### 配置正式客户端路径

业务配置只从正式客户端的 config.json 读取,mini 配置用于指定路径、凭证和调度开关:

```bash
cp config.example.json config.json
# 配置项:
#   client_config  -> 正式客户端 config.json 的地址
#   auth.refresh_token -> 登录凭证;留空则尝试自动解密同目录 refresh-token.enc
#                      (仅当 mini 与正式客户端跑在同一 Windows 用户下可解出;
#                       macOS/Linux 的 .enc 是 Keychain+AES 格式,解不了,必须手填)
#   data_dir       -> 可选,覆盖数据目录
#   code_dir       -> 可选,覆盖内核目录
#   state_dir      -> mini 自己的运行时状态目录,默认 ./state
python run.py --config config.json
```

mini 的取值规则(路径配置为空时使用默认值):

| mini 需要 | 来源 |
|---|---|
| `data_dir` | mini 的 `data_dir`,缺省读正式 config 的 `settings.all_data_path` |
| `code_dir` | mini 的 `code_dir`,缺省为 `{data_dir}/code`(不读取环境变量) |
| `state_dir` | mini 的 `state_dir`,缺省为 `./state` |
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

## mini 配置说明

`client_config` 可选,未填写或为空时默认使用 `%APPDATA%\QuantclassClient\config.json`。
mini 的 `config.json` 不存在时也使用此默认地址,不会创建 mini 配置文件。
未提供其他路径配置时,数据目录取正式配置,内核目录为 `data_dir/code`,
状态目录为 mini 配置路径所在目录下的 `state`。
未配置 `client_config` 且未设置 `APPDATA`,或正式客户端配置不存在时会报错。
不支持把业务配置直接放在 mini 配置中独立运行。
`client_config`、`data_dir`、`code_dir`、`state_dir` 的相对路径均以
mini 配置文件所在目录为基准。
正式配置的 `settings.all_data_path` 相对路径以正式配置所在目录为基准。
路径覆盖不会修改正式客户端配置。

| 键 | 说明 |
|---|---|
| `auth.refresh_token` | 登录凭证;留空尝试解密正式配置同目录的 `refresh-token.enc` |
| `base_url` | API 地址,默认 `https://api.quantclass.cn` |
| `auto_real_trading` / `auto_update` / `auto_min_data` | 调度开关,mini 配置优先于正式配置 |
| `min_data_mode` | 分钟数据模式,mini 配置优先于正式配置 |

`server_port`、`real_market_config`、`schedule`、`pos_mgmt.strategies` 等业务配置
仍读取正式客户端。`real_market_25.json` 需由正式客户端提前生成,mini 不写入。
内核需手动放置在 `code_dir/{kernel}/`,mini 不自动下载。

## fuel 临时路径环境变量

mini 配置可填写 `"fuel_temp_file_path": "Z:/stockdata/.fuel-temp"`。
非空相对路径以 mini 配置文件所在目录为基准,启动 fuel 时传入
`FUEL_TEMP_FILE_PATH` 并记录该路径;未配置或为空时仍传空字符串。
该配置不影响其他内核,不自动创建文件或目录。调用方显式传入的
`extra_env` 字符串或其中的 `FUEL_TEMP_FILE_PATH` 优先于此配置。

**此功能只保证环境变量被传入,尚未确认 fuel 是否将它用作 parquet 合并临时目录。**
需通过 fuel 实际输出验证,不能据此认定跨盘移动问题已解决。

## 启动时同步本地内核

`MiniConfig.default_code_dir` 固定为 `data_dir/code`,由数据目录派生,无需额外填写 JSON。
配置了 `code_dir` 且它不在默认目录内时,`run.py` 会在启动服务和调度器前调用
`client_mini/kernal_copy.py` 的 `copy_kernels()`:

- 按 fuel、fusion、aqua、rocket、scm 的 `.yml` 文件名比较版本,不请求远程接口。
- 版本不同或目标二进制缺失时,从默认目录复制对应内核的完整目录和版本标记。
- 替换目标内核目录并清理该内核旧版本标记,其他内核及无关文件不受影响;源目录只读。
- 版本相同则跳过;源版本或二进制缺失时记录日志并保留目标。
- 先复制到临时目录,成功后再替换;复制失败、源版本标记不唯一或目标内核忙时停止启动。
- `code_dir` 与默认目录相同或在其子目录下时不复制;禁止将其设为默认目录的父目录。

请在正式客户端退出、源内核更新完成后启动 mini,不要在同步期间运行或更新内核。

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
   (两者使用同一回调端口,不能同时运行,见上)

## refresh_token 从哪来

正式客户端登录后写在 userData 的 `refresh-token.enc`,是 Electron safeStorage 密文:

- **mini 与正式客户端同一 Windows 用户运行**(如都在虚拟机内):留空
  `auth.refresh_token` 即可,mini 自动解密(safe_storage.py)。支持裸 DPAPI,
  以及 Windows `v10` 格式:读取同目录 `Local State` 的 `os_crypt.encrypted_key`,
  经 DPAPI 解出密钥后使用系统 CNG 验证并解密 AES-256-GCM,无需第三方依赖。
  `Local State` 与 `refresh-token.enc` 必须匹配,不要只跨机器复制凭证文件。
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
3. 拷贝 client-mini 目录进虚拟机,`copy config.example.json config.json`,
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
