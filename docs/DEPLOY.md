# 部署到 Linux 云主机

本文档假设：Ubuntu 22.04 / 24.04 或 Debian 12，具备 root 或 sudo 权限，
已能通过 SSH 登录。整套流程约 10 分钟。

---

## 1. 上传代码

```bash
# 在本地打包（排除逆向中间产物与数据）
cd /path/to/AutoSinIn
tar --exclude='./data' --exclude='./tools/src-wx0e47' \
    --exclude='./tools/*.wxapkg' --exclude='./__pycache__' \
    -czf autosinin.tar.gz .

# 上传
scp autosinin.tar.gz root@你的服务器IP:/tmp/
```

服务器上：

```bash
sudo mkdir -p /opt/autosinin
sudo tar -xzf /tmp/autosinin.tar.gz -C /opt/autosinin
cd /opt/autosinin
```

---

## 2. 安装运行环境

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tzdata
sudo timedatectl set-timezone Asia/Shanghai      # 关键：签到窗口按服务器时区计算

sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt
```

> **时区非常重要。** 签到窗口 21:00–22:30 按服务器本地时间计算。
> 若服务器在 UTC，实际会在北京时间凌晨 5 点签到。请务必设置为 `Asia/Shanghai`。

---

## 3. 创建专用用户与目录

```bash
# --user-group 必须加：否则不会创建同名用户组，
# systemd 单元的 Group=autosinin 会导致服务启动失败（status=216/GROUP）
sudo useradd -r -s /usr/sbin/nologin -d /opt/autosinin --user-group autosinin || true

# 确认用户与组都存在（应输出两行）
id autosinin
getent group autosinin

sudo mkdir -p /opt/autosinin/data
sudo chown -R autosinin:autosinin /opt/autosinin
sudo chmod 750 /opt/autosinin/data
```

> `data/` 里存的是数据库和加密密钥，务必只让服务用户可以读写。

---

## 4. 配置

```bash
sudo cp deploy/env.example /opt/autosinin/.env
sudo nano /opt/autosinin/.env
```

最少需要确认两项：

| 变量 | 说明 |
|---|---|
| `TIMEZONE` | 保持 `Asia/Shanghai` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` | 见下方生成方式 |

生成管理员密码哈希：

```bash
cd /opt/autosinin
sudo .venv/bin/python tools/set_admin.py hash --password '你的强密码'
# 把输出的哈希填到 .env 的 ADMIN_PASSWORD_HASH，用户名填 ADMIN_USERNAME

# ⚠️ 关键：.env 必须对 autosinin 用户可读。
# systemd 的 EnvironmentFile 若读不到会**静默忽略**，
# 表现为"服务能起来但配置全不生效、管理员登不进去"。
sudo chown autosinin:autosinin /opt/autosinin/.env
sudo chmod 600 /opt/autosinin/.env
sudo -u autosinin cat /opt/autosinin/.env >/dev/null && echo ".env 可读 OK"
```

> 若跳过这一步，首次打开 `/admin` 页面会提示"首次设置管理员"，
> 直接在网页上设置也可以。**设置完成后该入口会自动关闭。**

---

## 5. 注册为系统服务

```bash
sudo cp deploy/autosinin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autosinin
sudo systemctl status autosinin
```

查看日志：

```bash
sudo journalctl -u autosinin -f
```

此时服务监听 `127.0.0.1:8000`，仅本机可访问。

### 5.1 上线前自检（强烈建议）

这个脚本会一次性查出所有"服务能起来但功能不工作"的坑
（时区不对、`.env` 读不到、密钥坏掉、校历没配、学校换客户端凭据……）：

```bash
cd /opt/autosinin
sudo -u autosinin .venv/bin/python tools/preflight.py --online
```

全部 `[ OK ]`、没有 `[FAIL]` 才算就绪。

---

## 6. Nginx 反向代理 + HTTPS

域名解析到服务器 IP 后：

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

`/etc/nginx/sites-available/autosinin`：

```nginx
server {
    listen 80;
    server_name checkin.example.com;

    client_max_body_size 4m;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/autosinin /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 申请证书（会自动改成 HTTPS）
sudo certbot --nginx -d checkin.example.com
```

完成后：
* 用户端 `https://checkin.example.com/`
* 管理端 `https://checkin.example.com/admin`

---

## 7. 首次使用清单

0. **先用真实账号验证签到链路**（最关键的一步，务必先做）：
   ```bash
   sudo -u autosinin .venv/bin/python tools/probe.py \
        --username 你的学号 --password '学校密码' --do-sign
   ```
   探针会打印验证码、登录、任务列表、任务详情、今日状态、本月记录，
   最后真的签一次。**这一步通过，后面的自动签到才有意义。**
   若登录就失败，请先在 <https://simp.csuft.edu.cn> 网页上确认密码能登进去。
1. 打开 `/admin` 登录；
2. （可选）进入 **校历管理** 添加本学期区间与寒暑假区间；
   > 校历**只影响日历看板的着色**，不决定是否打卡。
   > 是否打卡由学校任务的 `taskStartDate`~`taskEndDate` 与 `signWeek`
   > 决定 —— 它会覆盖寒暑假，所以寒假留校学生照常签到。
   > **不配置校历也能正常运行。**
3. 在 **总览** 点一次"校验某天是否会签到"，确认判定符合预期；
4. 用 **重新随机分配签到时刻** 确认时刻都落在 21:00–22:30 之间；
5. 把用户端地址发给同学，让他们提交申请；
6. 在 **待审批** 中逐个批准；
7. 同学回到用户端完成"首次绑定学校账号"。

---

## 8. 日常运维

```bash
# 查看统计与今日判定
sudo -u autosinin .venv/bin/python tools/set_admin.py stats

# 列出所有用户
sudo -u autosinin .venv/bin/python tools/set_admin.py list-users

# 手动补跑某天
sudo -u autosinin .venv/bin/python tools/set_admin.py run-daily 2026-03-10 --force

# 重置管理员密码
sudo .venv/bin/python tools/set_admin.py set-password --username admin
sudo systemctl restart autosinin
```

### 备份

需要备份的只有 `data/` 目录（数据库 + 密钥）：

```bash
sudo tar -czf ~/autosinin-backup-$(date +%F).tar.gz -C /opt/autosinin data
```

> `data/config.json` 里含 `ENCRYPTION_KEY`。**丢失它会导致已保存的学校账号密码全部无法解密**，
> 所有用户都需要重新绑定。

### 升级

```bash
cd /opt/autosinin
sudo systemctl stop autosinin
sudo tar -xzf /tmp/autosinin-new.tar.gz -C /opt/autosinin   # 不要动 data/
sudo chown -R autosinin:autosinin /opt/autosinin
sudo .venv/bin/pip install -r requirements.txt
sudo systemctl start autosinin
```

---

## 9. 常见问题

**Q：日志里出现"未配置学期"**
正常。到管理端 **校历管理** 添加学期区间即可。

**Q：某个用户一直签到失败**
管理端 **成员管理** 里有"最近错误"列，也可以在 **运行日志** 按学号过滤。
常见原因：
- `用户名或密码错误` —— 学校侧改过密码，让同学重新绑定；
- `该任务要求现场拍照，网页端无法完成` —— 这类任务只能手动在小程序打卡；
- `未找到打卡任务` —— 学校尚未发布打卡任务，或该生没有宿舍登记。
- `学校要求图形验证码` —— 学校临时启用了验证码，让同学重新绑定一次即可。

**Q：会不会重复签到？**
不会。每次提交前都会先调 `getOne` 检查当天状态，已完成则直接跳过。
签到后还会再查一次确认。

**Q：能不能多进程 / 多实例？**
不能。服务内置调度器，多实例会导致重复执行。
`run.py` 已强制 `--workers 1`，systemd 单元也是单进程。

**Q：服务器重启后还生效吗？**
`systemctl enable` 已设置开机自启。重启后调度器会自动恢复。

**Q：想临时停掉自动签到但保留网页？**
在 `.env` 里设 `SCHEDULER_DISABLED=1`，然后 `systemctl restart autosinin`。
