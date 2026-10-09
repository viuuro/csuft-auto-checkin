# 第三方组件声明

本项目使用或分发了以下第三方开源组件。各自遵循其原有许可证。
本文件仅作声明，不改变任何组件的许可条款。

---

## 服务端运行时依赖

`requirements.txt` 中声明的依赖，通过 pip 安装，不随本仓库分发。

| 组件 | 许可证 | 用途 |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | Web 框架 |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI 服务器 |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | HTTP 客户端 |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD-3-Clause | Fernet 加密 |
| [bcrypt](https://github.com/pyca/bcrypt) | Apache-2.0 | 口令哈希 |
| [APScheduler](https://github.com/agronholm/apscheduler) | MIT | 定时调度 |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | 表单解析 |

---

## 抓包工具依赖

`tools/token_catcher.py` 等脚本需要 mitmproxy，通过 pip 安装。

| 组件 | 许可证 | 用途 |
|---|---|---|
| [mitmproxy](https://github.com/mitmproxy/mitmproxy) | MIT | HTTPS 中间人代理 |

---

## 分发包内包含的组件

`tools/build_classmate_kit.py` 生成的分发包（`dist/checkin-auth-tool.zip`）
**内含**以下组件，因此分发时必须遵守其许可证：

| 组件 | 版本 | 许可证 | 说明 |
|---|---|---|---|
| [Python](https://www.python.org/) | 3.12.x 嵌入式版 | PSF License | 官方 embeddable 发行版 |
| [pip](https://github.com/pypa/pip) | 最新 | MIT | 由 get-pip.py 安装 |
| [mitmproxy](https://github.com/mitmproxy/mitmproxy) | 12.2.3 | MIT | HTTPS 中间人代理 |
| 及 mitmproxy 的传递依赖 | — | 见下 | 通过 pip 安装 |

mitmproxy 的主要传递依赖（各自许可证）：

| 组件 | 许可证 |
|---|---|
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD-3-Clause |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 |
| [h11](https://github.com/python-hyper/h11) / [h2](https://github.com/python-hyper/h2) | MIT |
| [kaitaistruct](https://github.com/kaitai-io/kaitai_struct_python_runtime) | MIT |
| [publicsuffix2](https://github.com/nexb/publicsuffix2) | MPL-2.0 / MIT |
| [pyasn1](https://github.com/pyasn1/pyasn1) | BSD-2-Clause |
| [pyOpenSSL](https://github.com/pyca/pyopenssl) | Apache-2.0 |
| [ruamel.yaml](https://bitbucket.org/ruamel/yaml) | MIT |
| [sortedcontainers](https://github.com/grantjenks/python-sortedcontainers) | Apache-2.0 |
| [tornado](https://github.com/tornadoweb/tornado) | Apache-2.0 |
| [wsproto](https://github.com/python-hyper/wsproto) | MIT |
| [zstandard](https://github.com/indygreg/python-zstandard) | BSD-3-Clause |
| [Brotli](https://github.com/google/brotli) | MIT |
| [aioquic](https://github.com/aiortc/aioquic) | BSD-3-Clause |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | MIT |
| [attrs](https://github.com/python-attrs/attrs) | MIT |
| [blinker](https://github.com/pallets-eco/blinker) | MIT |
| [click](https://github.com/pallets/click) | BSD-3-Clause |
| [Flask](https://github.com/pallets/flask) | BSD-3-Clause |
| [asgiref](https://github.com/django/asgiref) | BSD-3-Clause |

> mitmproxy 的 Windows 官方单文件版（PyInstaller 打包）**未**被本项目分发 ——
> 它自 2026-10-09 起被 Windows Defender 判为 PUA。分发包使用的是
> pip 安装形式，由内置 Python 承载。

---

## 前端

前端为手写原生 JavaScript / CSS，**不使用**任何前端框架或第三方库，
因此没有额外的前端依赖声明。

---

## 逆向分析参考

在分析微信小程序加密包的过程中参考了以下公开实现（**未包含在
本仓库中**，仅在分析阶段本地使用）：

- [pc_wxapkg_decrypt](https://github.com/hedgehoguis/pc_wxapkg_decrypt) —— 微信 PC 端小程序包解密

这些工具的代码未进入本仓库，也不构成对本项目的许可约束。

---

## 学校系统与本项目的关系

本项目分析的目标系统（`simp.csuft.edu.cn`，FlySource 平台）是**第三方
商业软件**，不是开源项目。本仓库**不包含**其任何源代码或二进制文件。

仓库中出现的接口路径、客户端标识（`client_id` / `client_secret`）等
信息，来源于该平台在公开分发的小程序前端代码中的常量 ——
它们是小程序的**应用级凭据**，不是任何个人的私密凭据。

如果你是该系统的权利人并认为本项目侵犯了你的权益，
请通过 [SECURITY.md](SECURITY.md) 中的方式联系，我会配合处理。
