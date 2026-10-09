# 安全策略

## 报告安全问题

如果你发现安全问题，请**不要**开公开 issue。

请通过以下方式私下联系：

1. 在 GitHub 上使用 **Security → Report a vulnerability**（推荐）
2. 或给维护者发私信

请在报告中包含：

- 问题描述与影响范围
- 复现步骤
- 受影响的版本 / 提交
- 你建议的修复方式（如有）

我会尽快确认并回复。修复发布后会在 Release Notes 中致谢
（除非你希望匿名）。

---

## 本项目的安全设计

### 凭据存储

| 项 | 处理方式 |
|---|---|
| 学校账号密码 | Fernet（AES-128-CBC + HMAC）对称加密后入库 |
| 会话令牌 / refresh_token | 同上 |
| 加密密钥 | 部署时随机生成，存于 `data/config.json`，权限 600 |
| 管理员口令 | bcrypt 哈希（cost 12），不可逆 |
| 用户查询口令 | bcrypt 哈希（cost 12） |

`data/` 目录、`.env`、`.deploy-keys/` 均已在 `.gitignore` 中。

### 部署隔离

- 服务以专用非特权用户 `autosinin` 运行（`nologin` shell）
- systemd 单元启用 `NoNewPrivileges`、`ProtectSystem=strict`、
  `PrivateTmp`，仅 `data/` 可写
- 不要把服务以 root 身份运行

### 抓包工具

分发包在本机会：

- 启动仅监听 `127.0.0.1` 的中间人代理
- 向「当前用户 → 受信任的根证书颁发机构」安装临时 CA
- 结束时自动卸载证书、关闭代理、删除本地 CA 私钥

**分发包不预置任何 CA 证书**，私钥由使用者的机器现场生成。

---

## 已知的安全权衡

### 1. 签到坐标来自学校登记的宿舍基准点

任务要求定位校验（`openLocate=1`）时，本项目提交学校自己登记的宿舍
基准坐标，使得 `locationAccuracy`（到基准点的距离）为 0 米。

**这意味着签到位置不代表真实位置。** 这是为了绕过定位校验而做的设计，
使用者应理解其性质（见 [DISCLAIMER.md](DISCLAIMER.md) 第二节）。

### 2. 会话令牌长期有效

本项目采用「绑定一次凭据 → 用 refresh_token 滚动续期」的模式，
因此一次授权可能长期有效。如果你想撤销：

- 在小程序里重新登录（会使旧 token 失效）
- 或在管理端删除该用户（会调用学校 `logOut` 接口使会话失效）
- 并删除服务器上的 `data/` 数据

### 3. 分发包体积与杀软误报

分发包内含 Python 运行时与 mitmproxy（约 36 MB / 解压后 150 MB+）。
mitmproxy 的中间人代理特性会导致**部分杀毒软件误报**。
这是该工具类别的常见现象。请自行判断是否信任。

---

## 不要做的事

在提交 issue、PR 或分享配置时，**不要**包含：

- 真实学号、姓名、手机号
- 服务器 IP、域名、SSH 凭据
- 任何 `access_token` / `refresh_token` 原文
- `data/` 目录内容或 `config.json`
- 抓包产物（`tools/capture/`）

仓库提供了自查工具：

```bash
# 先把你的真实标识写进 tools/.private-terms（该文件已被忽略）
python tools/audit_privacy.py
```

它会扫描仓库中所有文本文件，报告这些值出现的位置，
以及疑似学号/手机号/公网 IP/私钥块/硬编码口令等模式。
