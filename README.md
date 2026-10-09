# 中南林业科技大学平安打卡自动签到（仅供学习使用）

针对微信小程序 **中南林学工**（appid `wx0e47c34c9982aa09`）的宿舍签到自动化服务，
包含**用户网页**与**管理员网页**两部分。可自托管部署。

> **⚠️ 使用前请先读 [DISCLAIMER.md](DISCLAIMER.md)。**
> "代打卡"在多数高校属于违纪行为，本项目仅作接口协议分析与工程实践示例，
> 使用者需自行承担全部后果。作者与任何学校、任何第三方服务商均无关联。

> 技术基础：本项目的接口调用不是猜测出来的，而是把该小程序从微信 PC 端的加密包中
> 完整解密、解包并逐行还原其 `http/request.js` 签名拦截器与业务页面逻辑后得到的。
> 详见 [逆向过程](#附录逆向过程)。

**开源信息**：[许可证 MIT](LICENSE) ·
[免责声明](DISCLAIMER.md) ·
[安全策略](SECURITY.md) ·
[第三方组件](THIRD_PARTY_NOTICES.md)

---

## 功能一览

### 用户网页（`/`）

| 功能 | 说明 |
|---|---|
| 首次使用申请 | 填学号提交申请，**必须等管理员亲自批准** |
| 学校打卡账号登录 | 批准后绑定学号 + 学校密码，系统据此自动签到 |
| 每天自动签到 | 在 **21:00–22:30** 之间的**随机时刻**执行 |
| 自动跳过寒暑假 | 按管理员维护的校历（学期 + 假期区间）判定 |
| 日历看板 | 月视图，绿=成功 / 红=失败 / 黄=待签到 / 灰=假期无需签到 |
| 仅凭学号查看 | 只能查自己的：学号 + 首次绑定时自设的**查询口令** |

### 管理员网页（`/admin`）

| 功能 | 说明 |
|---|---|
| 唯一管理员登录 | **有且仅有**管理员本人的账号可登录，其余一律拒绝 |
| 亲自审批 | 新用户申请必须手动批准 / 拒绝（可填原因），批准后才可用 |
| 成员管理 | 启用 / 停用、重置查询口令、手动签到、撤回授权、删除 |
| 校历管理 | 维护学期区间、寒暑假、节假日、调休上课 |
| 运行监控 | 实时统计、今日是否签到、手动触发、失败重试、运行日志 |

---

## ⚠️ 重要的现实约束：登录方式

学校账号**启用了短信二次认证**（`isSysUserSecondAuth: true`），实测结论：

| 方式 | 结果 |
|---|---|
| 学号 + 密码 换 token（`grant_type=password`） | ✗ 撞上二次认证，**走不通** |
| 抓取同学在小程序里的登录会话 | ✓ **唯一可行路径**（实测验证） |

因此同学授权采用**一次性采集**模式：同学在小程序里登录一次，
管理员用工具抓取该次会话的 token，加密入库后长期复用。

```bash
python run.py                            # 先启动服务
python tools/token_catcher.py 20240001   # 引导式采集，逐步提示
```

工具会引导完成：安装临时证书 → 启动代理 → 同学登一次小程序 →
抓取 token → **自动卸载证书并关闭代理** → 加密入库 → 生成查询口令。

完整说明与失败排查见 **[docs/ONBOARDING.md](docs/ONBOARDING.md)**。

> token 实测有效期约 **18–20 天**，到期后需要重新采集一次。
> 系统会在到期前把该用户标记出来，管理端可见。

---

## 快速开始（本机）

```bash
pip install -r requirements.txt
python run.py                       # http://127.0.0.1:8000
```

打开 <http://127.0.0.1:8000/admin>，首次会提示**设置管理员账号**（密码至少 8 位）。
设置完成后该入口自动关闭。随后：

1. **校历管理** → 添加本学期区间，以及寒暑假区间；
   > ⚠️ 未配置学期前，系统**不会**执行任何自动签到（刻意的安全开关）。
2. 把用户端地址发给同学 → 同学提交申请 → 你在**待审批**里批准；
3. 同学回到用户端完成"首次绑定学校账号"，自动签到即刻生效。

部署到云主机见 **[docs/DEPLOY.md](docs/DEPLOY.md)**：

```bash
python tools/deploy_remote.py --host <你的服务器> --user root
# 默认装到 /opt/autosinin，可用 --remote-dir 改到别的路径
```

### 抓包工具分发包

学校账号普遍开启了短信二次认证，无法直接用账号密码登录，
需要用户在小程序里授权一次、抓取会话令牌。可以给使用者打包一个
**免安装**工具（内置 Python 与 mitmproxy，解压双击即用）：

```bash
python tools/build_classmate_kit.py            # 产出 dist/checkin-auth-tool.zip
```

分发包默认使用中性文案（称呼「管理员」、结果文件 `token-result.txt`）。
如果你想换成自己的名字，用环境变量定制：

```bash
# Linux / macOS
ASI_ADMIN_NAME="你的名字" ASI_RESULT_FILE="发给你的名字.txt" \
  python tools/build_classmate_kit.py

# Windows PowerShell
$env:ASI_ADMIN_NAME = "你的名字"
python tools/build_classmate_kit.py
```

> **分发包不含任何 CA 证书或私钥** —— 证书由使用者的机器在首次运行时
> 现场生成，跑完自动删除。这是为了公开分发时不出现「所有人共用一把
> CA 私钥」的隐患。
>
> 分发前建议先跑 `python tools/verify_kit.py` 自查包内是否夹带了
> 本机路径或真实信息。

### 命令行快捷方式

不想走网页也可以直接录入：

```bash
# 批准并直接绑定学校账号（会打印查询口令，仅显示一次）
python tools/add_user.py 20240001 --name 你的名字 --password '学校密码'
```

---

## ⚠️ 上线前必须完成的一步：用真实账号跑一次探针

**`stuSign` 的请求体是逐字段还原的，但尚未在真实环境验证过。**
本机的接口调用链路已经用假客户端全量测试，学校侧能否接受这次签到，
必须在拿到真实账号后确认一次：

```bash
python tools/probe.py --username 学号 --password 密码          # 只读探测
python tools/probe.py --username 学号 --password 密码 --do-sign  # 真正签一次
```

`probe.py` 会依次打印：图形验证码 → 登录 → 任务列表 → 任务详情 →
今日状态 → 本月记录。**如果卡在第 2 步"用户名或密码错误"**，说明密码不对
（服务端此时回的是"用户名或密码错误"而非"用户名不存在"，即账号本身是存在的），
请先在 <https://simp.csuft.edu.cn> 网页上确认能登进去。
若学校要求走统一身份认证（CAS），需要另行适配 `grant_type=cas` 流程。

---

## 配置

所有配置项都是可选的，优先级：**环境变量 > `data/config.json` > 内置默认值**。
完整清单见 [`deploy/env.example`](deploy/env.example)。常用几项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SIGN_WINDOW_START` / `SIGN_WINDOW_END` | `21:00` / `22:30` | 自动签到窗口 |
| `TIMEZONE` | `Asia/Shanghai` | 窗口按此时区计算（**服务器时区也要对**） |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | 空 | 写死管理员账号（可选） |
| `SCHEDULER_DISABLED` | `0` | 置 `1` 只保留网页、关闭自动签到 |
| `FLYSOURCE_BASE_URL` | `https://simp.csuft.edu.cn` | 学校服务器（若变更可改） |

`data/config.json` 中的 `SECRET_KEY` 与 `ENCRYPTION_KEY` 首次启动自动生成。
**`ENCRYPTION_KEY` 丢失会导致所有已保存的学校密码无法解密，用户需重新绑定。**

---

## 工作原理

### 签到时间窗

1. 每位用户绑定成功后，系统在 21:00–22:30 之间**随机**分配一个分钟粒度的时刻
   （`users.plan_sign_time`），各人不同、不可预测。管理员可一键重新随机。
2. 调度器在窗口内每分钟检查一次，到点即执行。
3. 窗口结束前 10 分钟自动补试当天失败记录；23:30 从学校服务端同步真实结果校准日历。

### 签到判定链

每次执行前依次检查：

1. 用户已批准且未被停用；
2. 校历判定为签到日（学期内、非假期；调休上课优先于假期）；
3. 当天尚未打卡（先调 `getOne` 查询，已完成直接跳过，**不会重复签到**）；
4. 任务不要求现场拍照；
5. 用宿舍登记坐标提交 `stuSign`，提交后再查一次确认。

任何一步失败都会写入 `sign_records` 与 `sign_logs`，在管理端可见。

> ⚠️ **判定「是否已签到」必须看 `signTime`，不要看 `signStatus`。**
> 实测签到成功后 `signStatus` 仍为 `0`（`signStatusName='正常'`），
> 而 `0` 极易被误读成「未打卡」；未签的那天是 `signStatus=3`（未签）。
> 详见 [docs/VERIFICATION.md §七](docs/VERIFICATION.md)，
> 实现见 `app/services.py` 的 `already_signed()`。

### 服务端复刻的签名算法

```
签名输入 = 相对路径(去掉 query) + "?sign="
FlySource-sign = md5(签名输入 + md5(str(ts) + token)) + "1." + base64(str(ts))
FlySource-Auth = <access_token>          # 原始 token，不加 bearer 前缀
Authorization  = Basic base64("<ClientId>:<ClientSecret>")
```

> ⚠️ 两个易错点（已用真实抓包向量写成回归测试）：
> ① 必须用**相对路径**（`/api/xxx`）而非完整 URL，且**剔除 query**；
> ② token 用**原始值** —— 加 `bearer ` 前缀会算出不同签名，请求会被拒。

请求体中的 `stuTaskId` 是对
`{latitude, longitude, locationAccuracy, signDate, taskId, fileId}` 的紧凑 JSON 取 MD5，
字段顺序与小程序完全一致。

### 真实验证状态

签名算法与全部业务接口**已在真实环境验证通过**（用抓包取得的真实凭据），
详见 **[docs/VERIFICATION.md](docs/VERIFICATION.md)**：

| 项目 | 状态 |
|---|---|
| 签名算法与小程序逐字节一致 | ✓ 已用 2 组抓包向量验证 |
| `getListForApp` / `getTaskByIdForApp` | ✓ 拉取成功 |
| `getOne` / `getUserListAppByMonth` | ✓ 拉取成功 |
| `stuSign` 提交 | ✓ 服务端已受理解析，仅因时间窗口拒绝 |
| 窗口内签到成功 | ⏳ 待 21:00–22:30 实测确认 |

实测到的任务参数：签到时段 **21:00–22:30**、`openLocate=1`、`openTakePhoto=0`
（无需拍照，可完全自动化）、基准坐标 `28.1311, 112.9947`。

涉及的学校接口：

| 用途 | 接口 |
|---|---|
| 登录 | `POST /api/flySource-auth/oauth/token` |
| 任务列表 | `GET /api/flySource-yxgl/dormSignTask/getListForApp` |
| 任务详情 | `GET /api/flySource-yxgl/dormSignTask/getTaskByIdForApp` |
| 月记录 | `GET /api/flySource-yxgl/dormSignRecord/getUserListAppByMonth` |
| 当日状态 | `GET /api/flySource-yxgl/dormSignRecord/getOne` |
| **签到** | `POST /api/flySource-yxgl/dormSignRecord/stuSign` |

---

## 安全设计

| 项目 | 做法 |
|---|---|
| 学校密码 / token | Fernet 对称加密后存库，密钥独立于数据库 |
| 管理员与查询口令 | bcrypt（cost 12）哈希，不存明文 |
| 浏览器会话 | HMAC-SHA256 签名的 HttpOnly Cookie，带过期时间 |
| 越权防护 | 用户端所有查询强制以会话中的学号为准，无法查看他人 |
| 管理端 | 单账号校验 + 登录失败限速（8 次 / 10 分钟）+ 全量审计日志 |
| 误伤保护 | 未配置学期 = 不签到；任务要求拍照 = 拒绝盲签 |

> **合规提示**：请仅用于本人账号，并遵守学校相关规定。本项目不绕过任何身份认证，
> 使用的是用户本人提供的账号密码，调用的是小程序自身的公开业务接口。

---

## 项目结构

```
AutoSinIn/
├── run.py                     启动入口
├── requirements.txt
├── app/
│   ├── main.py                FastAPI 应用与生命周期
│   ├── config.py              配置加载（自动生成密钥）
│   ├── constants.py           共享常量
│   ├── db.py                  SQLite 数据层
│   ├── security.py            哈希 / 加密 / 会话签名
│   ├── flysource.py           学校接口客户端（含签名算法）
│   ├── calendar_rules.py      校历判定
│   ├── services.py            业务逻辑（申请/审批/绑定/签到）
│   ├── scheduler.py           APScheduler 调度器
│   ├── routes_user.py         用户端 API
│   ├── routes_admin.py        管理端 API
│   └── static/                前端（原生 JS，无需构建）
│       ├── index.html  app.js
│       ├── admin.html  admin.js
│       └── style.css
├── deploy/
│   ├── autosinin.service      systemd 单元
│   └── env.example            环境变量模板
├── docs/DEPLOY.md             云主机部署指南
└── tools/                     逆向脚本、探针与测试
```

## 测试

一条命令跑完全部检查：

```bash
python tools/verify_all.py            # 本地检查
python tools/verify_all.py --online   # 额外探测学校接口
```

单项工具：

| 命令 | 作用 |
|---|---|
| `python tools/test_units.py` | 49 项：签名算法、加密、校历判定、配置 |
| `python tools/test_e2e.py` | 56 项：申请→审批→绑定→签到→管理全链路（用假客户端） |
| `python tools/check_requirements.py` | 11 条需求逐条映射到实现与测试证据 |
| `python tools/check_wiring.py` | 前端 JS 引用的 DOM id 是否都有来源 |
| `python tools/preflight.py --online` | 上线前自检：时区/密钥/权限/校历/学校接口 |
| `python tools/probe.py --username 学号 --password 密码` | 对真实学校接口做只读探测 |

`preflight.py` 是部署时最该先跑的一个 —— 它能一次性揪出
"服务起得来但功能不工作"的常见原因（`.env` 属主不对导致配置被静默忽略、
服务器时区不是 `Asia/Shanghai`、加密密钥损坏、校历没配、学校更换客户端凭据等）。

`probe.py` 是排查线上问题最有效的工具：它按顺序打印验证码、登录、任务列表、
任务详情、今日状态、本月记录，加 `--do-sign` 才会真正签到。

---

## 附录：逆向过程

<details>
<summary>展开查看（含每一步的中间产物）</summary>

### 1. 定位小程序

微信 PC 端把小程序包存在：

```
%APPDATA%\Tencent\xwechat\radium\users\<用户>\applet\packages\<appid>\<版本>\__APP__.wxapkg
```

通过 `applet\local\<appid>\usr\miniprogramLog\log1` 中的页面路径
（`pages/index/shouye`、`pages/login/account`、`pages/views/stusign`）
确认目标 appid 为 `wx0e47c34c9982aa09`。

### 2. 解密容器

包以 `V1MMWX` 开头，是微信 PC 端的加密封装：

```
"V1MMWX"(6B) | AES-128-CBC(1024B) | XOR 混淆(其余)
AES key = PBKDF2-HMAC-SHA1(appid, "saltiest", 1000, 32)[0:16]
AES IV  = "the iv: 16 bytes"
XOR key = appid 的倒数第二个字符（本包为 '0' = 0x30）
```

### 3. 提取与解包

解密后是标准 `wxapkg`。它的文件索引并非完全连续存放
（样本中前 48 条记录连续，第 49 条夹在数据区中间），
因此本项目**只固化了解密这一步**，明文本身已足够检索出全部业务逻辑：

```bash
# 从微信 PC 端缓存取出加密包后
node tools/decrypt-package.mjs <__APP__.wxapkg> wx0e47c34c9982aa09 dec.wxapkg

# 直接在明文里检索
grep -a -o 'FlySource-sign' dec.wxapkg
grep -a -o 'dormSignRecord/stuSign' dec.wxapkg
grep -a -o 'flysource_wise_wxapp' dec.wxapkg
```

### 4. 还原签名与接口

关键文件 `app-service.js` 中的 `http/request.js` 给出了签名算法，
`http/authUtil.js` 给出了 `BaseURL` 与客户端凭据，
`http/index.js` 列出了全部接口，`pages/views/stusign.js` 给出了签到的完整请求体。

### 5. 找到可用的登录方式

小程序只允许 `grant_type=wxapp`，服务端明确拒绝 `password`：

```json
{"error":"invalid_client","error_description":"Unauthorized grant type: password"}
```

但网页端 SPA（`simp.csuft.edu.cn/js/index-*.js`）里有另一套客户端凭据
（`ClientId="flySource"`），它**允许** `grant_type=password`。
于是网页端可以用"学号 + 密码"直接从服务端换取 token——这正是本项目的登录路径。

</details>
