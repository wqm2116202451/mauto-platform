# MAuto 智能助手平台

依据《MAuto 智能助手平台 产品需求文档 V1.0》实现的本地可运行版本。后端 Python（FastAPI + SQLite），前端为无构建步骤的原生 ES Modules 单页应用。

## 快速开始

```bash
cd /Users/mairi/AI/2026-11-16/mauto-platform
./scripts/run.sh
```

默认地址：<http://127.0.0.1:8848>

演示账号：`demo@mauto.ai` / `demo1234`（登录页支持一键填入）。

首次启动会自动创建 SQLite 数据库并写入行业资讯、插件、PPT 模板等演示数据。

## 目录结构

```
mauto-platform/
├── src/mauto/            # 后端源码
│   ├── app.py            # FastAPI 应用（挂载 API、静态资源、SPA 回退）
│   ├── serve.py          # 生产启动入口（uvicorn 多 worker、stdout 日志）
│   ├── config.py         # 环境变量配置（监听、CORS、Cookie、缓存、日志）
│   ├── api/              # chat / tasks / news / plugins / ppt / projects / misc
│   ├── schema.sql        # 数据库结构
│   ├── seed.py           # 演示数据
│   ├── llm.py            # 内置演示模型引擎（可切换真实 OpenAI 兼容接口）
│   ├── scheduler.py      # 定时任务调度（多进程下文件锁保证单实例）
│   └── ppt_render.py     # PPTX / PDF / PNG 渲染
├── static/               # 前端源码（HTML / CSS / ES Modules）
├── data/                 # 运行时产物：SQLite、上传文件、导出文件（已忽略）
├── scripts/run.sh        # 本地启动脚本
├── Dockerfile            # 生产镜像
├── docker-compose.yml    # 单机部署编排
├── .env.example          # 环境变量模板
├── DEPLOY.md             # 域名 / DNS / HTTPS / 部署与备份说明
└── requirements.txt
```

## 配置

| 环境变量 | 说明 |
| --- | --- |
| `MAUTO_ENV` | 运行环境：`development`（默认）/ `production` |
| `MAUTO_HOST` / `MAUTO_PORT` | 监听地址与端口，默认 `127.0.0.1:8848`；容器内为 `0.0.0.0:8000` |
| `MAUTO_LAN` | 设为 `1` 时监听 `0.0.0.0`、自动探测局域网 IP，并在 HTTP 下关闭 Secure Cookie |
| `MAUTO_BASE_URL` | 对外访问地址，如 `https://mauto.com`；不填时开发环境自动推导 |
| `MAUTO_WORKERS` | 生产进程数，默认 `1` |
| `MAUTO_DATA_DIR` | 数据目录（SQLite / 上传 / 导出），默认 `./data` |
| `MAUTO_CORS_ORIGINS` | 允许的跨域来源，逗号分隔；生产默认同源 |
| `MAUTO_ALLOWED_HOSTS` | Host 白名单，默认 `*` |
| `MAUTO_COOKIE_SECURE` / `MAUTO_COOKIE_SAMESITE` / `MAUTO_COOKIE_DOMAIN` | 登录 Cookie 策略 |
| `MAUTO_STATIC_CACHE_SECONDS` | 静态资源缓存秒数；开发 `0`，生产 `86400` |
| `MAUTO_LOG_LEVEL` | stdout 日志级别，默认 `info` |
| `MAUTO_FORWARDED_ALLOW_IPS` | 信任的反向代理地址 |
| `MAUTO_ENABLE_SCHEDULER` | 设为 `0` 时关闭内置任务调度 |
| `MAUTO_LLM_API_KEY` | 配置后对话改走真实 OpenAI 兼容接口 |
| `MAUTO_LLM_BASE_URL` | 兼容接口地址，默认 `https://api.openai.com/v1` |
| `MAUTO_LLM_MODEL` | 模型名，默认 `gpt-4o-mini` |
| `MAUTO_SEARCH_ENGINE` | 检索引擎：`auto`（默认，搜狗优先、Bing 兜底）/ `sogou` / `bing` |
| `MAUTO_SEARCH_MAX_RESULTS` | 返回结果条数，默认 5 |
| `MAUTO_SEARCH_FETCH_PAGES` | 实际抓取正文的网页数，默认 3 |
| `MAUTO_SEARCH_TIMEOUT` | 单次网络请求超时秒数，默认 6 |
| `MAUTO_SEARCH_DISABLE` | 设为 `1` 时关闭联网检索 |
| `MAUTO_CA_BUNDLE` | 自定义 CA 证书路径（系统 Python 缺根证书时使用） |
| `MAUTO_NEWS_REFRESH_MINUTES` | 资讯自动刷新间隔（分钟），默认 30 |
| `MAUTO_NEWS_MAX_ITEMS` | 资讯最多保留条数，默认 240 |
| `MAUTO_NEWS_TIMEOUT` | 单个 RSS 源抓取超时（秒），默认 8 |
| `MAUTO_NEWS_DISABLE` | 设为 `1` 时关闭资讯抓取 |

## 生产运行与部署

```bash
# 生产入口（uvicorn 多 worker、日志输出到 stdout、支持反向代理头）
MAUTO_ENV=production MAUTO_HOST=0.0.0.0 MAUTO_PORT=8000 MAUTO_WORKERS=2 \
MAUTO_BASE_URL=https://mauto.com PYTHONPATH=src .venv/bin/python -m mauto.serve

# 健康检查
curl -s http://127.0.0.1:8000/healthz

# Docker
cp .env.example .env && docker compose up -d --build
```

局域网（同一 Wi-Fi 的手机/电脑）调试：

```bash
MAUTO_LAN=1 ./scripts/run.sh
# 其他设备访问 http://<本机局域网IP>:8848
```

`mauto.local`（macOS Bonjour）、单机 hosts 指向 `mauto.com`、防火墙与 HTTP 能力限制见
[DEPLOY.md](DEPLOY.md) 的「局域网访问」章节。

完整的 VPS / Docker / Render / Railway 部署、`mauto.com` DNS 解析、HTTPS 证书与备份步骤见
[DEPLOY.md](DEPLOY.md)。

## 资讯数据

「咨询」模块不再使用演示数据，启动时会从公开 RSS 源抓取真实资讯并写入 SQLite：

- 来源：36氪、IT之家、虎嗅、钛媒体、量子位、极客公园、爱范儿、Solidot、芥末堆、
  少数派，以及中新网（健康 / 教育 / 财经 / 滚动）；
- 字段：真实标题、摘要、来源、发布时间与原文链接；正文优先取 RSS 内容，
  详情页在正文过短时按需抓取原网页补全；
- 归类：按标题与摘要中的关键词自动归入 10 个行业，综合源中无法归类的条目会被丢弃；
- 刷新：启动时自动同步，之后每 30 分钟后台刷新；也可在页面点「刷新」或调用
  `POST /api/v1/news/refresh` 手动触发；抓取结果缓存 10 分钟；
- 失败：抓取失败或无数据时，接口与页面会显示真实错误原因，不会退回编造的演示内容；
- AI 摘要：由真实正文做句子级摘录生成摘要与要点（标注「MAuto-Air·摘录」），
  不做推测性补全；收藏、订阅、推荐 / 最新 / 热榜、搜索筛选均基于同一批真实数据。

## 联网检索

对话页「联网搜索」开关打开后，服务端会真实抓取搜索结果与网页正文（仅使用 Python 标准库，
无需搜索 API Key），再把正文摘录整合成带编号引用的回答，引用卡片可直接跳转原文。

- 引擎：默认搜狗网页搜索（中文问题相关性更好），失败或无结果时自动回退 Bing；
- 正文：优先 `article` / `main` 区域，过滤脚本、导航、广告等噪声后做句子级打分摘录；
- 失败：检索超时、被限流或无结果时，回答会明确说明失败原因并建议调整问法，不会退回模板编造；
- 结果缓存 10 分钟，避免重复抓取。

## 演示数据

- 10 个行业分类
- 14 个公共插件（覆盖办公、数据、内容、生活、开发）
- 12 套 PPT 模板（6 个场景 × 多种风格）
- 3 条演示会话，用于展示「最近」列表与继续对话

## 说明

- 未配置模型密钥时使用内置演示模型：规则化生成中文回答、推理过程、引用与插件调用结果，流式输出与真实模型一致。
- 开启「联网搜索」时，即使没有模型 Key，回答也会基于真实抓取的网页正文自动摘录生成；配置 Key 后改由模型基于同一批检索资料做 RAG 总结。
- 插件调试为本地沙箱模拟，不会真正请求第三方服务。
- PDF / PNG 导出由 Pillow 排版渲染，PPTX 由 python-pptx 生成，均支持中文。
- 所有时间以 `Asia/Shanghai` 展示，调度按任务配置时区执行。
