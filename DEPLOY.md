# MAuto 部署说明（mauto.com）

本文档覆盖从零到上线的完整流程：本地验证 → 服务器或托管平台 → DNS 解析 → HTTPS → 更新与备份。
代码不写死主机、端口与域名，全部通过环境变量注入。

## 1. 运行形态

```
浏览器 ──HTTPS──> Nginx / Caddy（80/443） ──HTTP──> MAuto（8000）
                                                   ├── FastAPI（/api/v1）
                                                   ├── 静态资源（/static）
                                                   └── SQLite / 上传 / 导出（/app/data）
```

- 进程入口：`python -m mauto.serve`（uvicorn，多 worker、stdout 日志、反向代理头）
- 健康检查：`GET /healthz`（返回服务、环境、数据库状态；数据库异常时 503）
- 数据目录：默认 `./data`，容器内为 `/app/data`，必须持久化并定期备份
- 同一时刻只有一个 worker 运行任务调度器（文件锁保证），新闻抓取也只由一个进程完成

## 2. 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAUTO_ENV` | `development` | `production` 时启用同源 CORS、HTTPS Cookie、静态长缓存等默认值 |
| `MAUTO_HOST` | `127.0.0.1` | 监听地址；容器内必须是 `0.0.0.0` |
| `MAUTO_LAN` | `0` | 设为 `1` 时监听 `0.0.0.0`、探测局域网 IP，并默认使用 HTTP 与非 Secure Cookie |
| `MAUTO_PORT` | `8848` | 监听端口；容器内为 `8000` |
| `MAUTO_BASE_URL` | 开发环境自动推导 | 对外访问地址，如 `https://mauto.com`；生产建议显式设置 |
| `MAUTO_DATA_DIR` | `<项目>/data` | SQLite、上传、导出目录 |
| `MAUTO_WORKERS` | `1` | uvicorn 进程数；生产建议 `2` |
| `MAUTO_CORS_ORIGINS` | 开发 `*`，生产同源 | 逗号分隔的允许来源，如 `https://mauto.com,https://www.mauto.com` |
| `MAUTO_ALLOWED_HOSTS` | `*` | Host 头白名单，生产建议填域名 |
| `MAUTO_COOKIE_SECURE` | 生产或 HTTPS 时 `true` | 登录 Cookie 是否仅 HTTPS 传输 |
| `MAUTO_COOKIE_SAMESITE` | `lax` | `lax` / `strict` / `none`（`none` 强制 Secure） |
| `MAUTO_COOKIE_DOMAIN` | 空（当前主机） | 需要跨子域共享登录态时设置，如 `.mauto.com` |
| `MAUTO_STATIC_CACHE_SECONDS` | 开发 `0`，生产 `86400` | `/static/*` 缓存时长；`index.html` 始终不缓存 |
| `MAUTO_LOG_LEVEL` | `info` | 日志级别，输出到 stdout |
| `MAUTO_FORWARDED_ALLOW_IPS` | `127.0.0.1` | 信任的反向代理地址，用于解析 `X-Forwarded-*` |
| `MAUTO_ENABLE_SCHEDULER` | `1` | 设为 `0` 可关闭内置任务调度 |
| `MAUTO_LLM_API_KEY` 等 | 空 | 真实模型配置；不填时使用内置演示引擎 |

完整示例见 `.env.example`。真实密钥只放在 `.env` 或托管平台的环境变量里，`.env` 已被 Git 忽略。

## 3. 本地验证

```bash
cd /Users/mairi/AI/2026-11-16/mauto-platform

# 方式一：原启动脚本（开发默认 127.0.0.1:8848）
./scripts/run.sh

# 方式二：生产入口（单进程或指定 worker 数）
MAUTO_ENV=production MAUTO_PORT=8001 MAUTO_WORKERS=2 \
MAUTO_BASE_URL=https://mauto.com \
PYTHONPATH=src .venv/bin/python -m mauto.serve

curl -s http://127.0.0.1:8001/healthz
```

## 3.1 局域网访问（手机 / 同一 Wi-Fi 的其他电脑）

### 启动

```bash
# 监听所有网卡；自动探测局域网 IP、放行局域网 Host/CORS，并关闭 HTTP 下的 Secure Cookie
MAUTO_LAN=1 ./scripts/run.sh

# 等价写法（开发环境；生产环境请用 MAUTO_LAN=1，或显式设置 MAUTO_COOKIE_SECURE=false）
MAUTO_HOST=0.0.0.0 ./scripts/run.sh
```

启动日志会输出实际监听地址、`lan_ip` 与 `cookie_secure`。查看本机局域网 IP：

```bash
ipconfig getifaddr en0      # Wi-Fi；有线网卡通常是 en1/en2
```

假设得到 `192.168.1.23`，其他设备打开：

```
http://192.168.1.23:8848
```

- `MAUTO_HOST` 默认仍是 `127.0.0.1`，不影响原来的本机访问方式；
- `MAUTO_LAN=1` 时默认 `cookie_secure=false`，HTTP 局域网下登录态可正常保存；
- 如果显式设置了 `MAUTO_ALLOWED_HOSTS` / `MAUTO_CORS_ORIGINS`，配置会自动补充局域网 IP、`localhost`、`mauto.local`，无需手改；
- 仅建议在可信局域网临时使用；不要在公网直接暴露 8848 端口。

### 验证

```bash
curl -s http://192.168.1.23:8848/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.23:8848/static/css/app.css
curl -s -X POST http://192.168.1.23:8848/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@mauto.ai","password":"demo1234"}'
```

### 用 `mauto.local` 访问（macOS Bonjour / mDNS）

`mauto.com` 是公共域名，无法指向内网；同一局域网可用 mDNS 名称：

```bash
# 把本机 Bonjour 名称设为 mauto（需要管理员权限）
sudo scutil --set LocalHostName mauto

# 验证解析（macOS）
dscacheutil -q host -a name mauto.local
ping -c 2 mauto.local
```

然后 iPhone / iPad / Mac 打开：

```
http://mauto.local:8848
```

注意：
- iOS/macOS 原生支持 `.local`；Windows 需安装 Bonjour（iTunes/iCloud 附带）；
- Android 多数系统不解析 mDNS，建议直接用上面的局域网 IP；
- 端口 8848 不能省略；想用 `http://mauto.local`（80 端口）需要 root 绑定 80 或加反向代理。

### 单台设备把 `mauto.com` 指向局域网 IP（仅该设备有效）

这只修改本机 hosts，不影响公共 DNS：

- macOS / Linux：`sudo vi /etc/hosts`，加入
  ```
  192.168.1.23 mauto.com www.mauto.com
  ```
  刷新缓存：
  ```bash
  sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
  ```
- Windows：以管理员身份编辑 `C:\Windows\System32\drivers\etc\hosts`，加入同一行，然后
  ```powershell
  ipconfig /flushdns
  ```
- iOS 不能直接改 hosts：可改用 `mauto.local`，或在局域网内搭一台 DNS（Pi-hole / AdGuard Home /
  dnsmasq）添加 `mauto.com → 192.168.1.23`，再把 iPhone 的 Wi-Fi DNS 指向该服务器。
- 浏览器访问 `http://mauto.com:8848`；需要把 `MAUTO_ALLOWED_HOSTS` 设为 `mauto.com,www.mauto.com`
  并保持 `MAUTO_LAN=1`，否则严格 Host 白名单会拦截。

### 防火墙与网络限制

- macOS：系统设置 → 网络 → 防火墙，允许 Python/终端接收传入连接；首次启动弹窗选「允许」。
- 路由器/公司 Wi-Fi 若开启「AP 隔离 / 客户端隔离」，手机无法访问电脑，需要关闭或换网络。
- VPN、代理软件可能改变路由或拦截局域网地址，测试时先关闭。
- 电脑休眠后服务不可达，需保持唤醒。

### HTTP 局域网的功能限制

非 HTTPS 且非 localhost 时，浏览器不把页面视为「安全上下文」，以下能力可能不可用：

- 剪贴板 API（复制分享链接可能失败，页面会退化为手动选择）；
- 摄像头 / 麦克风（`getUserMedia`）、定位、部分传感器；
- Service Worker / PWA 安装、部分 Web Crypto 能力。

登录 Cookie 已通过 `MAUTO_COOKIE_SECURE=false` 适配 HTTP；如需完整能力，请在局域网用
Caddy/Nginx 配本地 HTTPS 证书，或部署到有正式证书的域名。

## 4. VPS + Docker（推荐）

### 4.1 安装与启动

```bash
# 服务器上安装 Docker 与 Compose 插件后
git clone <你的仓库地址> mauto-platform
cd mauto-platform

cp .env.example .env
# 编辑 .env：至少确认 MAUTO_BASE_URL、MAUTO_ALLOWED_HOSTS、MAUTO_CORS_ORIGINS
docker compose up -d --build

docker compose ps
curl -s http://127.0.0.1:8000/healthz
```

`docker-compose.yml` 默认只把 8000 端口绑定在 `127.0.0.1`，对外由反向代理提供 HTTPS。
如需直接暴露端口，把 `ports` 改为 `"8000:8000"`，并在防火墙放行。

数据保存在命名卷 `mauto-data` 中，重建容器不会丢失。

### 4.2 不用 Docker

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

MAUTO_ENV=production MAUTO_HOST=127.0.0.1 MAUTO_PORT=8000 MAUTO_WORKERS=2 \
MAUTO_BASE_URL=https://mauto.com MAUTO_DATA_DIR=/var/lib/mauto \
PYTHONPATH=src ./.venv/bin/python -m mauto.serve
```

建议用 systemd 托管（示例）：

```ini
[Unit]
Description=MAuto Platform
After=network.target

[Service]
WorkingDirectory=/opt/mauto-platform
EnvironmentFile=/opt/mauto-platform/.env
Environment=PYTHONPATH=/opt/mauto-platform/src
ExecStart=/opt/mauto-platform/.venv/bin/python -m mauto.serve
Restart=always
User=mauto

[Install]
WantedBy=multi-user.target
```

## 5. DNS 绑定 mauto.com

1. 在域名注册商/云解析控制台添加记录：
   - `A  @   <服务器公网 IPv4>`（Apex 域名）
   - `AAAA @ <服务器 IPv6>`（有 IPv6 时可选）
   - `CNAME www @`（或直接给 `www` 加一条 A 记录）
2. TTL 建议先用 300 秒，验证通过后再调大。
3. 验证解析：
   ```bash
   dig +short mauto.com
   dig +short www.mauto.com
   ```
4. 云服务器安全组/防火墙放行 80、443（以及必要的 22）。

> 需要你提供：域名 DNS 管理权限、服务器公网 IP（或托管平台分配的域名）。

## 6. HTTPS

### 方案 A：Caddy（自动证书，最简单）

```caddyfile
mauto.com, www.mauto.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo caddy reload --config /etc/caddy/Caddyfile
```

Caddy 会自动申请并续期 Let's Encrypt 证书。

### 方案 B：Nginx + certbot

```nginx
server {
    listen 80;
    server_name mauto.com www.mauto.com;
    return 301 https://mauto.com$request_uri;
}

server {
    listen 443 ssl http2;
    server_name mauto.com;

    ssl_certificate     /etc/letsencrypt/live/mauto.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mauto.com/privkey.pem;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;   # 对话流式输出与 PPT 生成较慢
        proxy_buffering off;
    }
}
```

```bash
sudo certbot --nginx -d mauto.com -d www.mauto.com
```

证书签发后，确认 `.env` 中 `MAUTO_BASE_URL=https://mauto.com`、`MAUTO_COOKIE_SECURE=true`。

## 7. Render / Railway 等托管平台

1. 新建 Web Service，连接 GitHub 仓库，选择 Dockerfile 构建。
2. 健康检查路径填 `/healthz`，容器端口 `8000`。
3. 配置环境变量：至少 `MAUTO_ENV=production`、`MAUTO_BASE_URL=https://mauto.com`、
   `MAUTO_ALLOWED_HOSTS=mauto.com`、`MAUTO_DATA_DIR=/app/data`；需要真实模型时再加 `MAUTO_LLM_API_KEY`。
4. 挂载持久化磁盘到 `/app/data`（SQLite、上传与导出文件都在这里；无持久盘时重启会丢数据）。
5. 在平台「Custom Domain」中添加 `mauto.com`，按提示到 DNS 添加平台给出的 CNAME/A 记录；
   HTTPS 证书由平台自动签发。
6. 托管平台通常只有单实例，`MAUTO_WORKERS` 保持 `1` 即可。

## 8. 更新发布

```bash
cd mauto-platform
git pull
docker compose up -d --build
docker compose ps
curl -s https://mauto.com/healthz
```

Nginx/Caddy 配置不变；静态资源带版本号（`?v=`），生产缓存 1 天，`index.html` 不缓存，发布后刷新即可生效。

## 9. 备份与恢复

需要备份的是数据目录：

```bash
# Docker 命名卷
docker run --rm -v mauto-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/mauto-data-$(date +%F).tar.gz -C /data .

# 裸机
tar czf mauto-data-$(date +%F).tar.gz -C /var/lib/mauto .
```

恢复时停止服务，把文件解压回 `data/`（或容器卷）再启动。

## 10. 首次推送到 GitHub

仓库已在本地初始化（分支 `main`），代码与部署文件已暂存；首次提交前先配置 Git 身份：

```bash
cd /Users/mairi/AI/2026-11-16/mauto-platform

git config user.name "你的名字"
git config user.email "you@example.com"
git commit -m "chore: 初始化 MAuto 平台与部署配置"

git remote add origin git@github.com:<你的账号>/<仓库名>.git
git push -u origin main
```

后续更新：

```bash
git add -A && git commit -m "描述本次改动"
git push
```

> 推送需要你提供 GitHub 仓库地址与凭证（SSH Key 或 PAT），本仓库不会自动推送。
> 如需改用 HTTPS：`git remote add origin https://github.com/<账号>/<仓库名>.git`。

## 11. 上线检查清单

- [ ] `https://mauto.com/healthz` 返回 `status: ok`、`db: ok`
- [ ] 首页、登录、对话流式输出正常
- [ ] 静态资源响应头包含 `Cache-Control: public, max-age=86400, immutable`，`index.html` 为 `no-cache`
- [ ] 登录响应 `Set-Cookie` 含 `Secure`、`HttpOnly`、`SameSite=Lax`
- [ ] `data/` 已挂载持久化，且有备份
- [ ] 仓库中没有 `.env`、`data/`、`*.db`、上传与导出文件
