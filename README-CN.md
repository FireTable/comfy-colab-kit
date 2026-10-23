<p align="center">
  <img src="assets/logo/logo-e-fusion-dark-512.png" width="96" height="96" alt="Comfy Colab Kit logo" style="border-radius: 16px;" />
</p>

<h1 align="center">Comfy Colab Kit</h1>

<p align="center">
  <b>随时可打开、按需唤醒的 Colab ComfyUI —— 打开网址；睡着就点“唤醒”看它启动；醒来后就是你的 ComfyUI 应用。</b>
</p>

<p align="center">
  <a href="README.md">English</a> •
  简体中文
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-amber.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3b82f6?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Colab-L4-F9AB00?logo=googlecolab&logoColor=white" alt="Colab L4" />
</p>

---

> [!IMPORTANT]
> **仅供个人使用。** Colab 算力属于你的 Google 账号，开放给他人使用可能违反 Colab 条款与 FAQ（如禁止转售或共享算力），
> 有封号风险。建议只给自己或极少数家人朋友使用。本项目与 Google、Comfy Org 无隶属关系。

---

## 📖 概述

VPS 上运行一个很小的网关 **comfy-gate**（Python / aiohttp），前面是 Cloudflare Access。它的体验像休眠的 Hugging Face Space：
GPU 关着时显示唤醒卡片和 Profile 选择；点 **唤醒** 后，所有正在看的人都看到同一份分阶段进度和实时日志；ComfyUI 就绪后同一网址
直接变成 ComfyUI（默认 App 模式，一键切到节点图）。闲置、超时或超预算自动休眠。

| 支柱 | 用户看到什么 | 实现 |
|---|---|---|
| **1 按需唤醒** | 休眠卡片 → 唤醒 → 阶段与日志 → ComfyUI | 10 状态状态机，`colab new`（L4）+ `colab exec` 初始化，同主机反代（含 `/ws`） |
| **2 可切换 Profile** | 唤醒前选择 MOSS-Image、SD1.5 等 | `profile.yaml` + JSON Schema；零密钥，模型按公开 URL 引用 |
| **3 自动停机** | 浮层显示剩余时间；闲置 / 会话上限 / 静默时段 / CU 预算触发休眠 | 全部规则写在 `.env` |

## ✨ 特性

- **全局唯一共享会话**：唤醒是幂等的，第二个访客（或 Agent）会加入正在进行的启动，不会再开一台 VM；状态写入
  `STATE_DIR/session.json`，网关重启后自动接管。
- **所有人看到同一份实时进度**：SSE 广播状态、下载 GB / 速度、初始化日志。
- **校验 Access 身份**：验证 `Cf-Access-Jwt-Assertion`（RS256、AUD、issuer）；`OWNER_EMAILS` / `OWNER_ONLY_ACTIONS` 限制切换、停机、续期。
- **后端只认服务令牌**：Colab 隧道主机名只放行网关的 Access 服务令牌。
- **极速冷启动**：`hf_xet` 并行下载、`uv` 安装、隧道最先启动、核心模型到位即启动 ComfyUI（`FAST_START`）。
- **App 模式优先**：工作流写入 ComfyUI App 模式元数据；注入的浮层可在 App ⇄ 节点图之间切换。
- **ComfyUI 配置透传**：每个 profile 可设置命令行参数、前端设置、环境变量、`extra_model_paths`。
- **对 Agent 友好**：OpenAPI、`cli/ckit --json`、`docs/AGENT_SKILL.md`、`llms.txt`。

## 🚀 快速开始（VPS + Cloudflare）

```bash
git clone <本仓库> comfy-colab-kit && cd comfy-colab-kit
cp .env.example .env                         # 填 PUBLIC_HOST、COLAB_TUNNEL_HOST、CF_ACCESS_*、邮箱
mkdir -p secrets data && chmod 700 secrets data
ZONE_NAME=example.com COLAB_TUNNEL_HOST=comfy-colab.example.com \
  CLOUDFLARE_API_TOKEN=… scripts/cf-setup.sh # 隧道 + DNS + 后端 Access 应用 + 服务令牌 → secrets/
openssl rand -hex 32 > secrets/agent_token && chmod 600 secrets/*
docker compose -f deploy/docker-compose.yml run --rm comfy-gate colab sessions   # 一次性 colab 登录
docker compose -f deploy/docker-compose.yml up -d
# 把 deploy/Caddyfile.snippet 加进 Caddy 并 reload，打开 https://$PUBLIC_HOST
```

完整步骤见 [docs/DEPLOY_VPS.md](docs/DEPLOY_VPS.md) 与 [docs/CLOUDFLARE.md](docs/CLOUDFLARE.md)，从零开始申请凭证与部署指南详见 [docs/AGENT.md](docs/AGENT.md)。

## 🧩 Profile

| Profile | 用途 | 默认下载量 |
|---|---|---|
| [`moss-image`](profiles/examples/moss-image/)（主力） | MOSS-Image：默认 DeBand int8，4 步加速、官方高质量 / 布局、编辑（可选），在 App 模式里并排对比 | 核心约 26 GB / 默认全集约 57 GB |
| [`sd-latest`](profiles/examples/sd-latest/) | Stable Diffusion 3.5：默认 Large fp8，TurboX 8 步，Medium 加速（需授权，可选） | 核心约 15 GB / 约 22 GB |
| [`qwen-image`](profiles/examples/qwen-image/) | Qwen-Image 2.1：默认 Turbo 文生图，完整文生图，Turbo / 完整编辑 | 核心约 27 GB / 约 43 GB |

工作流**只用**社区已发布的版本，从作者处下载并记录来源与许可证。格式与透传选项见 [docs/PROFILES.md](docs/PROFILES.md)。

## ⏱️ 自动停机

`IDLE_TIMEOUT_MIN` · `MAX_SESSION_HOURS` / `EXTEND_MAX_TIMES` · `BOOT_TIMEOUT_MIN` · `STOP_ON_PAGE_CLOSE_MIN` ·
`QUIET_HOURS` · `DAILY_CU_BUDGET` / `MONTHLY_CU_BUDGET` / `MIN_CU_BALANCE` · `ORPHAN_SWEEP_MIN` ·
`WATCHDOG_GATE_LOST_MIN`（Colab 侧自释放）。详见 [docs/AUTO_STOP.md](docs/AUTO_STOP.md)。

## 🔐 安全

密钥永不入库：`.env`、`secrets/`、`data/` 已忽略；pre-commit 与 CI 运行 gitleaks + `scripts/check-secrets.sh`；profile
拒绝类似凭据的字段。密钥经 `colab exec` 的 stdin 进入 Colab，只存在于内核内存。详见 [docs/SECURITY.md](docs/SECURITY.md)。

## 🏗️ Monorepo 工程结构

Comfy Colab Kit 采用工程化 Monorepo 组织架构，清晰分离服务应用、运行时包、模型预设与基础设施配置：

```
comfy-colab-kit/
├── apps/
│   ├── gate/                 # [应用] 网关与反向代理服务
│   │   ├── gate/             # 后端 Python 代码（aiohttp、状态机、CU 预算、反代）
│   │   │   └── web/          # 唤醒页 UI（index.html、bg.js、overlay.js）
│   │   └── openapi.yaml      # OpenAPI 3.1 接口定义
│   └── site/                 # [应用] 产品官网与架构展示静态站点
│       ├── index.html        # 宣传站入口
│       ├── styles.css        # 一致性暗黑/明亮主题样式
│       └── main.js           # 前端交互与动效
├── packages/
│   ├── colab/                # [运行时包] 远端 Google Colab 内核代理脚本
│   │   └── setup.py          # 容器打包上传并在 Colab VM 执行的环境装配脚本
│   ├── cli/                  # [开发工具包] 宿主机与智能体运维命令行
│   │   └── ckit              # Python 标准库独立 CLI 工具
│   └── schemas/              # [规范包] 预设 JSON Schema 约束定义
│       └── profile.schema.json
├── profiles/                 # 模型预设与工作流管理
│   ├── examples/             # 内置社区预设（moss-image、sd-latest、qwen-image）
│   └── custom/               # 用户挂载自定义预设目录
├── deploy/                   # 基础设施与部署编排
│   ├── Dockerfile            # 最小化容器镜像构建文件
│   ├── docker-compose.yml    # VPS 多容器互联编排
│   └── Caddyfile.snippet     # 反向代理路由规则配置
├── docs/                     # 完整工程规范与产品技术文档
│   ├── AGENT.md              # VPS 完整实操部署与凭证申请指南（英文）
│   ├── AGENT_SKILL.md        # AI Agent 自动化调度指南（唤醒、出图、关机）
│   └── ...
├── scripts/                  # 验证、校验与敏感信息审计脚本
└── tests/                    # 完整自动化单元与集成测试套件（108+ 测试）
```

根目录下维护有软链接（`gate/`、`site/`、`colab/`、`cli/`、`schemas/`、`examples/`），确保对已有环境、部署脚本及挂载完全向后兼容。

## 📄 许可证

本仓库 MIT。profile 引用的模型、LoRA、自定义节点与工作流保留各自许可证（见各 profile README），均从原始来源下载。
Colab 是 Google 的商标；ComfyUI 归 Comfy Org 所有；本项目与二者无隶属关系。
