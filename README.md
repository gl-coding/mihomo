# Mihomo 本地代理

独立仓库：[gl-coding/mihomo](https://github.com/gl-coding/mihomo)。在 nginxRouter 中通常作为 **Git 子模块** 挂载于 `devtools/mihomo/`（路径不变，平台 `MIHOMO_DIR` 默认仍指向此处）。

[Mihomo](https://github.com/MetaCubeX/mihomo)（原 Clash Meta 内核）在本机提供 **HTTP/SOCKS 出站代理** 与 **REST 控制 API**，供服务器访问外网（npm、Codex CLI 等），并为 nginxRouter 管理台 **「代理管理」**（`/proxy`）提供节点列表、切换、延迟测速、出口 IP 查询。

默认仅监听 **127.0.0.1**，不对外暴露代理端口。

## 命令行管理（run.sh）

### 安装 `mihomo` 管理命令

在仓库目录执行：

```bash
./install-cli.sh
# 默认创建 ~/.local/bin/mihomo → 本仓库 cli.sh
# 自定义安装目录：./install-cli.sh --bin-dir /your/bin
```

安装脚本不会覆盖已有同名命令，可重复执行。需将安装目录加入 `PATH`（本机已配置）。入口支持符号链接和任意工作目录，未指定参数时进入 Textual；现有 `run.sh` 的启动行为保持不变。

```bash
mihomo                            # 进入 Textual
mihomo tui                        # 显式进入 Textual
mihomo --help                     # 查看全部子命令
mihomo status                     # 内核状态与版本
mihomo start                      # 后台启动；已运行时返回状态
mihomo groups                     # 列出代理组、当前选择和是否支持手动切换
mihomo nodes --group '代理组名称'   # 查看指定组的全部节点（包括 DIRECT 等内置策略）
mihomo select '节点名称' --group '代理组名称'
mihomo mode                       # 查询当前代理模式
mihomo mode rule                  # rule / global / direct
mihomo system-proxy               # 默认查询当前用户桌面系统代理
mihomo system-proxy on            # 开启，off 关闭，status 查询
mihomo sub list                   # 订阅管理沿用现有 sub 命令
mihomo groups --json              # JSON 输出，便于脚本处理
mihomo --json mode                # --json 也可前置
mihomo sub list --json            # 嵌套子命令也支持后置 --json
```

- `groups` 列出手动及自动策略组；自动组标记为只读。`select` 仅允许切换手动 Selector 组，并读取内核状态确认结果；不传 `--group` 时自动解析主策略组。
- `nodes` 不传 `--group` 时保留原主策略组列表行为。`mode` 不带参数只查询；设置模式不改变系统代理开关。系统代理开关也不改变内核转发模式。
- `system-proxy` 支持 KDE / GNOME，开启前检查内核是否运行；无可用桌面时返回原因。网络操作无需 Web 服务。
- 新增管理命令的运行失败在 `--json` 模式返回 `{"ok": false, "message": "…"}` 并以状态码 1 退出；参数语法错误由命令帮助提示并返回状态码 2。
- 内核可执行文件仍位于 `bin/mihomo`；`mihomo` 命令是本仓库的管理入口。

**Textual 全屏终端管理（推荐）**：

```bash
cd ~/.privspec/common/devtools/mihomo
~/.shared_venv/bin/python -m pip install -r python/requirements.txt
./run.sh tui
# 也可通过 sp → 5「Mihomo 代理」→ 7「Textual 终端管理」进入
```

本机已安装并验证 Textual 8.2.8。启动脚本优先使用 `MIHOMO_PYTHON` 指定的解释器，其次使用 `~/.shared_venv/bin/python`；公共环境不存在时继续查找原有项目环境和系统 Python。共享环境的依赖锁文件位于 `~/.shared_venv/requirements.lock`。

界面直接复用 `python/mihomo_mgr` 业务逻辑，通过内核控制 API 工作，**无需启动独立 Web 管理服务**。采用与 Web 页一致的浅灰背景、白色卡片和蓝色强调色，默认进入概览页。顶部显示当前订阅流量、到期时间和当前代理组节点；支持鼠标及键盘，适合本机终端和 SSH：

| 页面 | 功能 |
|------|------|
| 概览 | 当前订阅更新 / 导入、当前节点测速与代理组 / 节点选择、系统代理 / 虚拟网卡页签（TUN 实际状态只读）、规则 / 全局 / 直连模式，全局模式直接选择 GLOBAL 出口，以及直连 / 代理出口 IP 和三个站点测试（支持一键验证、单站重测、详情弹窗与清空结果；仅保留概览、节点、订阅三页）；开关与模式按钮点击后立即应用，详情弹框展示实际配置 |
| 节点 | 左侧选代理组，右侧搜索节点、按名称或延迟排序；Enter 或「应用节点」切换，并读取内核状态确认；全局模式固定 GLOBAL，直连模式禁用选组与节点操作 |
| 订阅 | 查看流量、到期时间与当前订阅；导入、编辑、更新、启用、删除；链接输入隐藏，删除需确认，当前订阅不可删除 |

常用快捷键：`0` 返回概览，`F2` 切换明暗主题，`1`–`2` 切页（节点、订阅），`g` 聚焦代理组，`n` 聚焦节点，`/` 搜索，`↑↓` 移动，`Enter` 应用节点 / 启用订阅，`r` 刷新，`Tab` 切换控件，`q` / `Ctrl+Q` 退出。输入框内的普通字符用于输入，不触发全局快捷键。

- 状态每 15 秒自动刷新，编辑弹窗及操作期间暂停刷新。首页代理模式选择后立即生效，失败后恢复内核实际状态。
- 站点测试显式通过当前配置的 Mihomo HTTP 代理请求，不修改节点或策略组。HTTP 401 / 403 / 429 标记为站点受限，不与代理连接失败混淆。结果保存在当前会话；刷新和切换查看代理组时保留，内核启停、代理模式、实际组选择、当前订阅或代理目标改变后清除旧结果。
- 测速在后台并发执行（最多 4 个），结果逐项更新；「停止测速」停止发起后续请求，已发出的请求等待返回。订阅下载和首页验证不会阻塞界面。
- 操作进行中阻止重复修改及立即退出，避免后台写入被误认为取消；测速可先停止，再退出。退出界面不会停止 Mihomo，也不会关闭系统代理。
- 网络设置修改的是运行终端用户的桌面会话；SSH 无桌面会话时，系统代理会显示不可用，其余内核操作仍可使用。
- 需交互 TTY，非交互调用会给出说明并返回状态码 2。遵循终端的 `NO_COLOR` 设置；建议窗口至少 80×24；窄窗口的网络设置与代理模式卡片上下排列，内容可滚动。

原数字菜单及脚本接口继续保留：

```bash
./run.sh menu
# 或 ./run.sh ctl
```

与 Web/API 共用 `python/mihomo_mgr` 逻辑，非交互命令仅需 PyYAML，Textual 在进入 `tui` 时才加载：

```bash
./run.sh status
./run.sh sub list
./run.sh sub save --name 我的订阅 --url 'https://…' [--via-proxy|--direct]
./run.sh sub update [id]          # 从机场拉取更新
./run.sh sub activate <id>
./run.sh sub delete <id>
./run.sh nodes
./run.sh select '节点名'
./run.sh delay-batch [--workers 4]   # 主策略组批量测延迟；单节点请用 Web/API
./run.sh ip                       # 验证出口 IP（本机直连 + 代理，同步当前节点）
./run.sh verify-all               # 一键验证：出口 IP + 网站联通
./run.sh connectivity all         # 仅联通验证 google / chatgpt / ipify
```

非交互命令支持 JSON，例如 `mihomo status --json` 或 `./run.sh ctl --json status`，`--json` 可放在子命令前后；TUI 不输出 JSON。完整子命令：`./run.sh ctl --help`（与 `./run.sh status` 等等价）。

## 独立管理页（不依赖 nginxRouter）

与 nginxRouter 管理台 **「代理管理」**（`/proxy`）同一套页面与 API，可在本仓库单独启动：

```bash
./admin/start.sh
# 默认 http://127.0.0.1:8765/
```

- 后端逻辑在 `python/mihomo_mgr/`；nginxRouter 通过子模块路径复用同一代码。
- **首页概览**：第一行显示「当前订阅」与「当前节点」两张卡片。订阅卡展示名称、来源、节点数、流量进度、到期日和更新时间，可更新当前订阅或跳转订阅管理；节点卡展示所选策略组对应的节点、协议、UDP 与延迟，并提供「代理组 / 节点」两级可搜索选择器。规则模式可切换查看不同的手动策略组，选节点会立即应用到该组并读取实际结果；全局模式固定 `GLOBAL` 组并可切换其出口，直连模式显示 `DIRECT` 并禁用选择。可测试当前节点延迟，或跳转节点管理。窄屏下卡片上下排列。
- **网络设置 / 代理模式**：两张卡片分别控制桌面系统代理与 Mihomo 的规则 / 全局 / 直连模式；支持 Linux KDE Plasma / GNOME 系统代理，详见下方「系统代理与系统支持」。
- 订阅与环境变量写入 **`env/.env.mihomo.local`**（本仓根目录，与 `conf/`、`bin/` 同级；模板见 `env/.env.example`）。若仍存在主仓旧路径 `nginxRouter/env/.env.mihomo.local`，首次读写会自动复制到本仓 `env/`。
- CLI / Textual 依赖：`python/requirements.txt`；本机使用 `~/.shared_venv/bin/python -m pip install -r python/requirements.txt` 安装。
- 默认只监听本机，**无登录**（请勿将端口暴露到公网）。需要与平台一致的管理员鉴权时，请使用 nginxRouter 的 `/proxy`。

开发前端（热更新，API 代理到 8765）：

```bash
cd admin && npm install && npm run dev
# 另开终端：MIHOMO_STANDALONE=1 ./admin/start.sh --skip-build --port 8765
```

## 在本项目中的角色

| 端口 | 用途 |
|------|------|
| `7890` | HTTP / Mixed 代理 → 终端设置 `http_proxy` / `https_proxy`（见 `devtools/codex/env.sh`） |
| `9090` | External Controller API → `platform/backend/sites/mihomo_views.py` 转发 |

启动方式：

```bash
./devtools/mihomo/run.sh
# 或指定配置：MIHOMO_CONFIG=/path/to/config.yaml ./devtools/mihomo/run.sh
# 首次会从旧版平铺目录自动迁移到 bin / conf / cache
```

后台运行示例：

```bash
cd devtools/mihomo
nohup ./run.sh >> mihomo.log 2>&1 &
```

---

## 下载地址

**官方发布页（选最新版）：**

- https://github.com/MetaCubeX/mihomo/releases

Release 页有「二进制文件筛选 / Binary file selector」，可按系统选包。文档：

- https://wiki.metacubex.one/

### Linux 服务器常见包名

| 场景 | 推荐资产文件名（版本号以 Release 为准） |
|------|----------------------------------------|
| 常见 x86_64 云主机（支持 x86-64-v3） | `mihomo-linux-amd64-v3-vX.Y.Z.gz` |
| 较老 CPU / v3 无法运行 | `mihomo-linux-amd64-compatible-vX.Y.Z.gz` 或 `mihomo-linux-amd64-vX.Y.Z.gz` |
| ARM64（Graviton 等） | `mihomo-linux-arm64-vX.Y.Z.gz` |

是否支持 **v3** 可自检：

```bash
grep -q avx2 /proc/cpuinfo && echo "likely v3 ok" || echo "try compatible build"
```

**直链示例**（当前仓库内曾使用 **v1.19.32**，升级时请改版本号）：

```text
https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-v3-v1.19.32.gz
```

下载命令示例：

```bash
cd /path/to/nginxRouter/devtools/mihomo
wget -O mihomo-linux-amd64-v3-v1.19.32.gz \
  'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-v3-v1.19.32.gz'
```

---

## 下载后放哪里（目录布局）

运行文件按 **`bin` / `conf` / `cache`** 组织；`run.sh` 使用 **`cache/` 作为 Mihomo `-d` 工作目录**（`cache.db`、Geo 符号链接），配置文件在 **`conf/`**。

```
devtools/mihomo/
├── run.sh
├── bin/
│   ├── mihomo                 # 内核（由 .gz 解压）
│   └── mihomo-linux-*.gz      # 可选：保留官方包
├── conf/
│   ├── active.yaml            # 当前生效的完整配置（原 iKuuu_V2.yaml）
│   ├── profiles/{订阅名称}.yaml  # 各订阅下载缓存（与卡片名称一致）
│   └── geo/                   # GeoSite / GeoIP 等第三方规则库
│       ├── GeoSite.dat
│       ├── country.mmdb
│       └── geoip.metadb
└── cache/
    ├── cache.db               # Mihomo 运行缓存
    ├── mihomo.log
    └── → conf/geo/*           # 启动时符号链接，供 -d 读取
```

| 路径 | 是否进 git | 说明 |
|------|------------|------|
| `run.sh`、`README.md` | 是 | 启动与文档 |
| `bin/mihomo`、`bin/*.gz` | 否 | 可执行文件与压缩包 |
| `conf/active.yaml`、`conf/profiles/` | 否 | 运行配置与订阅缓存 |
| `conf/geo/*` | 否 | 规则库（含 GEOSITE 校验） |
| `cache/*` | 否 | 运行缓存与日志 |

旧版根目录下的 `mihomo`、`iKuuu_V2.yaml`、`profiles/` 等会在 **首次执行 `run.sh` 或管理台订阅操作** 时自动迁入上述结构。

### 解压并命名二进制

```bash
cd devtools/mihomo
mkdir -p bin
gunzip -k bin/mihomo-linux-amd64-v3-v1.19.32.gz   # 保留 .gz 则去掉 -k
mv bin/mihomo-linux-amd64-v3-v1.19.32 bin/mihomo
chmod +x bin/mihomo
./bin/mihomo -v
```

`run.sh` 固定执行 **`bin/mihomo`**，不要装到 `/usr/local/bin` 除非自行改脚本。

### 订阅 / 配置文件

1. 从机场获取 **Clash / Mihomo 订阅链接**，用转换工具生成 YAML，或保存提供商给的配置。
2. 推荐在管理页 **导入订阅**；手动放置时使用 **`conf/active.yaml`**，或通过 `MIHOMO_CONFIG` 指定 `devtools/mihomo/conf/` 内路径。
3. 配置内 **`external-controller`** 建议为 `127.0.0.1:9090`，**mixed-port / port** 与项目约定一致（常见 `7890`），以便管理台与 `env.sh` 无需改代码。

**勿将订阅 URL、UUID、密码提交 git**（已在 `.gitignore` 中排除）。

---

## 目录内文件速查

| 文件 | 作用 |
|------|------|
| `run.sh` | 迁移旧布局 → 启动 `bin/mihomo -d cache -f conf/active.yaml` |
| `bin/mihomo` | Mihomo 内核二进制 |
| `conf/active.yaml` | 当前生效：节点、策略组、规则 |
| `conf/profiles/*.yaml` | 多订阅各自缓存，文件名与订阅名称一致 |
| `conf/geo/GeoSite.dat` 等 | 含 **GEOSITE** 规则必需（可从 [meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat) 下载） |
| `cache/cache.db` | DNS、规则等本地缓存 |
| `cache/mihomo.log` | 运行日志 |

---

## 使用代理

```bash
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
curl -s https://api.ipify.org && echo
```

管理台：**代理管理** → 需 Mihomo 已启动且 `9090` 可连。

## 系统代理与系统支持

### Ubuntu 桌面：通过 `sp` 菜单管理系统代理

在 privspec 的 Ubuntu 环境中，命令行系统代理入口位于 **`~/.privspec/user/ubuntu/sh/sp.sh`**，由 Ubuntu 包装菜单提供。本仓库的 `run.sh menu` 不包含这一系统设置入口；独立 Web 管理页也可通过下述「网络设置」栏目管理。

首次使用或更新脚本后，在 Bash 终端加载菜单：

```bash
source ~/.privspec/user/ubuntu/sh/sp.sh
sp
# 选择 5「Mihomo 代理」
```

建议先选择 **1「后台启动」**，确认内核运行，再选择 **11「开启系统代理」**。其他相关选项：

| 菜单项 | 作用 |
|--------|------|
| 11「开启系统代理」 | 将当前桌面的 HTTP、HTTPS、SOCKS 代理设为默认 `127.0.0.1:7890`，启用手动代理 |
| 12「关闭系统代理」 | 关闭当前桌面的系统代理；Mihomo 内核仍继续运行 |
| 13「查看系统代理状态」 | 根据当前桌面读取实际代理设置 |

可在开启前通过环境变量指定地址和端口；该设置只改变桌面代理目标，Mihomo 的监听配置需与之保持一致：

```bash
export MIHOMO_PROXY_HOST=127.0.0.1
export MIHOMO_PROXY_PORT=7890
sp
```

### 独立管理页：网络设置

在本机访问 `http://127.0.0.1:8765/`，找到 **网络设置** 栏目：

1. 「网络设置」卡片提供「系统代理 / 虚拟网卡」页签；系统代理开关点击后立即应用，状态以实际系统配置为准。开启前需先启动 Mihomo，关闭后内核仍运行。
2. 标题旁的刷新按钮可同步外部修改；齿轮打开系统代理详情，显示识别出的桌面、状态及 HTTP / HTTPS / SOCKS 地址。切回浏览器窗口时也会刷新状态。
3. 当前代理地址与 Mihomo 不一致时，可点击「修复地址」；原先使用 PAC 等其他模式时也会提示。开启系统代理会覆盖当前模式与手动代理地址。
4. 「虚拟网卡」页签显示从内核读取的 TUN 状态。目前仅展示状态，不提供 TUN 启停，开关为只读。

Web 端使用后端的 **`MIHOMO_HTTP_PROXY`** 作为代理目标，默认 `http://127.0.0.1:7890`，修改该环境变量后需重启管理服务。`sp` 菜单使用的是 `MIHOMO_PROXY_HOST` / `MIHOMO_PROXY_PORT`；自定义端口时请保持两处配置和内核监听一致。

设置修改的是**运行独立管理服务的电脑上、启动服务用户的桌面代理**。请从该用户的 KDE / GNOME 桌面会话中启动管理页。未识别桌面或缺少工具时会显示原因并禁用操作；设置失败会尝试恢复先前配置。接口为 `GET /api/mihomo/network/`（读取）与 `POST /api/mihomo/network/`（应用），仅限独立模式及本机回环请求；Web 不支持的其他系统不会尝试修改设置。

### 代理模式：规则 / 全局 / 直连

独立页参考 [Clash Verge Rev 的首页卡片设计](https://github.com/clash-verge-rev/clash-verge-rev/tree/dev/src/components/home)，将「网络设置」与「代理模式」并排展示，窄屏下上下排列。代理模式点击后立即应用，切换期间会禁用重复操作；失败时重新读取内核实际状态。

| 模式 | 进入 Mihomo 的流量如何转发 |
|------|---------------------------|
| 规则 | 按订阅配置中的规则匹配代理节点、策略组或直连 |
| 全局 | 统一交给 `GLOBAL` 策略组；可在卡片内选择实际出口。若出口为 `DIRECT`，页面会提示流量仍将直连 |
| 直连 | 通过内核直接连接目标，不经过代理节点 |

系统代理决定应用是否把流量交给 Mihomo，代理模式决定 Mihomo 收到流量后如何转发，两者独立。当前模式通过 Mihomo `/configs` API 读写并验证，接口为 `GET /api/mihomo/proxy-mode/` 与 `POST /api/mihomo/proxy-mode/`（`mode` 为 `rule`、`global` 或 `direct`）。与网络设置接口相同，仅限本机独立站；POST 使用 `X-Mihomo-Network: 1` 请求头。更改只作用于当前运行实例，不改写订阅 YAML；重启内核或重新加载订阅后，应以刷新读取的模式为准。

### 支持范围

以下范围描述的是 **本项目系统代理功能**（`sp` 菜单及独立管理页）；Mihomo 内核的跨平台能力与桌面系统代理配置是两回事。

| 系统 / 桌面 | 系统代理支持 | 实现与依赖 |
|-------------|--------------|------------|
| Ubuntu KDE Plasma | 支持 | 根据 `XDG_CURRENT_DESKTOP` / `DESKTOP_SESSION` 识别 KDE；用 `kwriteconfig6/5`、`kreadconfig6/5`（也兼容无版本号命令）读写当前用户的 `kioslaverc`，通过会话 D-Bus 通知应用重载 |
| Ubuntu GNOME | 支持 | 使用 `gsettings` 读写 `org.gnome.system.proxy`；需在当前用户的桌面会话中执行 |
| 其他 Linux 桌面 | 未专门适配 | `sp` 的非 KDE 分支尝试使用 GNOME 的 `gsettings`；命令成功不代表该桌面或浏览器会采用这些设置。Web 仅对识别出的 KDE / GNOME（含 Unity 标识）开放操作 |
| 无桌面的 Linux / SSH 会话 | 不作为桌面代理使用方式 | 按上文设置 `http_proxy` / `https_proxy`，或为应用显式指定代理；SSH 会话中的设置不能用于确认其他用户桌面的代理状态 |
| Windows / macOS | 此 Ubuntu 包装脚本未适配 | 需使用对应系统的代理设置或客户端；本目录中附带的内核文件为 Linux amd64 版本 |

KDE 开启时设置 `ProxyType=1`，关闭时设置 `ProxyType=0`；HTTP、HTTPS 和 SOCKS 地址均写入 `~/.config/kioslaverc`（使用自定义 XDG 配置目录时以实际目录为准）。GNOME 开启时设置 `mode=manual`，关闭时设置 `mode=none`。设置针对当前用户，会保留到下次修改；开启操作会覆盖已有的手动代理地址，关闭操作不会恢复之前的 PAC 或代理模式。

### 生效范围与 Chrome 排查

- 系统代理供遵循桌面代理设置的浏览器和应用使用，不会强制接管整台机器的所有流量，也不会自动开启 TUN。
- 终端工具按各自规则读取 `http_proxy` / `https_proxy` 等环境变量；关闭桌面系统代理不会清除终端变量。
- 开启系统代理不会自动启动 Mihomo。停止 Mihomo 前可先关闭系统代理，避免应用继续连接已停止的本地端口。
- Chrome 若配置了代理扩展、策略或启动参数，可能覆盖桌面设置。修改后先刷新页面；仍失败时完全退出 Chrome 后重新打开。
- 在 KDE 桌面上，应确认 KDE 的 `ProxyType`，仅看到 GNOME 的 `mode=manual` 无法证明 KDE 系统代理已开启。菜单 13 会按桌面选择对应配置。

可以先显式通过本机代理测试 Google，区分代理线路与浏览器设置问题：

```bash
curl --noproxy '' --proxy http://127.0.0.1:7890 \
  -I --connect-timeout 8 --max-time 20 https://www.google.com
```

若目标站点返回 HTTP 200，而 Chrome 仍显示“无法访问此网站”，继续检查浏览器使用的代理配置及页面底部的具体错误码。

## 更新 Geo 规则库（与订阅分开）

含 `GEOSITE` / `GEOIP` 的订阅依赖 `conf/geo/`，**不会在 Web「更新订阅」时自动拉取**（文件大、耗时长、与机场无关）。

在服务器上执行（默认 `auto`：若本机 `7890` 能访问 GitHub 则走代理）：

```bash
./devtools/mihomo/run.sh --update-geo
# 或
./devtools/mihomo/update-geo.sh --only geosite
```

可选环境变量：`MIHOMO_GEO_VIA_PROXY=true|false|auto`、`MIHOMO_GEO_TIMEOUT=180`。

**为何不放管理页一键更新：** 单次下载可能 1～3 分钟，若做成「点按钮等 HTTP 返回」，会占满 Gunicorn worker、易触发 Nginx/浏览器超时，页面像卡死。Clash Verge / Verge Rev 等客户端是在 **桌面进程里异步下载**（后台任务 + 进度/Toast），不阻塞整页；本仓库是 Django 同步请求，更适合 **SSH 脚本或 cron**，订阅更新仍走 Web。

## 管理页更新订阅

在 **代理管理 → 代理订阅 → 导入订阅** 中填写名称和完整 Clash / Mihomo YAML 订阅链接，选择直连或通过当前代理下载。可保存多个订阅，每张卡片提供更新、启用及编辑/删除操作。链接保存在 `devtools/mihomo/env/.env.mihomo.local`，YAML 缓存位于 `conf/profiles/`，文件名与订阅名称一致（如 `ikku.yaml`、`paoluz.yaml`），均不提交 GitHub；页面只显示来源域名。

点击卡片的 **更新** 后，平台下载并校验配置。未启用的订阅只更新缓存；当前订阅更新后重载。点击 **启用** 切换代理配置，优先使用缓存，没有缓存时先下载；服务器监听和控制接口设置保留，失败会还原旧配置。当前订阅不可删除，需先启用另一个订阅。代理未运行时仅更新配置文件，启动后生效。

与 **Clash Verge** 相同：多数机场在订阅 HTTP 响应里附带 `subscription-userinfo`（`upload` / `download` / `total` / `expire`）及可选的 `profile-update-interval`。管理台在 **每次从机场重新下载** 时解析这些头并展示流量、到期日与本地「更新时间」；若机场不提供该头则只显示节点数与更新时间。流量为机场侧统计，不是 Mihomo 本地计量。

当前为手动更新，不包含定时更新。下载限制为 5MB，节点与规则来自完整 YAML，普通 Base64 节点列表需要先转换。

---

## 相关代码

- 共享后端：`python/mihomo_mgr/`（nginxRouter 中 `platform/backend/sites/mihomo_*.py` 为薄封装）
- 独立管理页：`admin/start.sh`、`admin/standalone/`
- 嵌入平台前端：`platform/frontend/src/pages/MihomoPage.vue`（与独立页同源）
- Codex 走代理：`devtools/codex/env.sh`、`devtools/codex/README.md`（在 nginxRouter 仓内）
