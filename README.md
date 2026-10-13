# Mihomo 本地代理

独立仓库：[gl-coding/mihomo](https://github.com/gl-coding/mihomo)。在 nginxRouter 中通常作为 **Git 子模块** 挂载于 `devtools/mihomo/`（路径不变，平台 `MIHOMO_DIR` 默认仍指向此处）。

[Mihomo](https://github.com/MetaCubeX/mihomo)（原 Clash Meta 内核）在本机提供 **HTTP/SOCKS 出站代理** 与 **REST 控制 API**，供服务器访问外网（npm、Codex CLI 等），并为 nginxRouter 管理台 **「代理管理」**（`/proxy`）提供节点列表、切换、延迟测速、出口 IP 查询。

默认仅监听 **127.0.0.1**，不对外暴露代理端口。

## 独立管理页（不依赖 nginxRouter）

与 nginxRouter 管理台 **「代理管理」**（`/proxy`）同一套页面与 API，可在本仓库单独启动：

```bash
./admin/start.sh
# 默认 http://127.0.0.1:8765/
```

- 后端逻辑在 `python/mihomo_mgr/`；nginxRouter 通过子模块路径复用同一代码。
- 订阅与环境变量写入 **`env/.env.mihomo.local`**（独立运行时相对本仓库根目录；嵌入 nginxRouter 时仍使用主仓 `env/.env.mihomo.local`）。
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

在 **代理管理 → 代理订阅 → 导入订阅** 中填写名称和完整 Clash / Mihomo YAML 订阅链接，选择直连或通过当前代理下载。可保存多个订阅，每张卡片提供更新、启用及编辑/删除操作。链接保存在 `env/.env.mihomo.local`，YAML 缓存位于 `devtools/mihomo/conf/profiles/`，文件名与订阅名称一致（如 `ikku.yaml`、`paoluz.yaml`），均不提交 GitHub；页面只显示来源域名。

点击卡片的 **更新** 后，平台下载并校验配置。未启用的订阅只更新缓存；当前订阅更新后重载。点击 **启用** 切换代理配置，优先使用缓存，没有缓存时先下载；服务器监听和控制接口设置保留，失败会还原旧配置。当前订阅不可删除，需先启用另一个订阅。代理未运行时仅更新配置文件，启动后生效。

与 **Clash Verge** 相同：多数机场在订阅 HTTP 响应里附带 `subscription-userinfo`（`upload` / `download` / `total` / `expire`）及可选的 `profile-update-interval`。管理台在 **每次从机场重新下载** 时解析这些头并展示流量、到期日与本地「更新时间」；若机场不提供该头则只显示节点数与更新时间。流量为机场侧统计，不是 Mihomo 本地计量。

当前为手动更新，不包含定时更新。下载限制为 5MB，节点与规则来自完整 YAML，普通 Base64 节点列表需要先转换。

---

## 相关代码

- 共享后端：`python/mihomo_mgr/`（nginxRouter 中 `platform/backend/sites/mihomo_*.py` 为薄封装）
- 独立管理页：`admin/start.sh`、`admin/standalone/`
- 嵌入平台前端：`platform/frontend/src/pages/MihomoPage.vue`（与独立页同源）
- Codex 走代理：`devtools/codex/env.sh`、`devtools/codex/README.md`（在 nginxRouter 仓内）
