"""Mihomo 外部控制 API 代理（供管理台选节点、测 IP）。"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .config import load_config
from .layout import bin_path, log_path, migrate_legacy_layout, runtime_config_path
from .permissions import StandaloneOrAdmin

MAIN_SELECTOR = "🔰 选择节点"
# 验证 IP 时除主策略组外，尽量同步到常见「兜底/总出口」组，避免规则仍走固定美国节点
_IP_TEST_EXTRA_GROUPS = (
    "GLOBAL",
    "PROXY",
    "Proxy",
    "🐟 漏网之鱼",
    "🌍 国外媒体",
    "🦊 PAOLUZ",
    "🚀 节点选择",
    "节点选择",
)
_IP_URLS = (
    "https://api.ipify.org?format=json",
    "https://api.ip.sb/ip",
)
CONNECTIVITY_TARGETS = {
    "google": ("Google", "https://www.google.com/"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "ipify": ("IPify", "https://api.ipify.org?format=json"),
}


def _check_connectivity(target: str) -> dict:
    name, url = CONNECTIVITY_TARGETS[target]
    started = time.monotonic()
    item = {"id": target, "name": name, "url": url, "reachable": False,
            "state": "failed", "http_status": None, "ip": None}
    proxy = _http_proxy()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AI-Desk-Connectivity"})
    try:
        with opener.open(request, timeout=10) as response:
            item.update(reachable=True, state="success", http_status=response.status, message="访问正常")
            if target == "ipify":
                try:
                    item["ip"] = json.loads(response.read(4096)).get("ip")
                except (ValueError, AttributeError):
                    pass
    except urllib.error.HTTPError as exc:
        item.update(reachable=True, state="restricted" if exc.code in (401, 403, 429) else "failed",
                    http_status=exc.code, message=f"已连接，站点返回 HTTP {exc.code}")
        exc.close()
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        item["message"] = "连接超时" if isinstance(reason, TimeoutError) else "代理连接失败"
    item["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    item["checked_at"] = datetime.now(timezone.utc).isoformat()
    return item


@api_view(["POST"])
def mihomo_connectivity(request):
    targets = request.data.get("targets", list(CONNECTIVITY_TARGETS))
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(target, str) or target not in CONNECTIVITY_TARGETS for target in targets)):
        return Response({"ok": False, "message": "测试目标无效"}, status=400)
    targets = list(dict.fromkeys(targets))
    with ThreadPoolExecutor(max_workers=min(len(targets), 3)) as executor:
        items = list(executor.map(_check_connectivity, targets))
    return Response({"ok": True, "items": items})


def _mihomo_dir() -> Path:
    return load_config().mihomo_dir


def _mihomo_running() -> bool:
    code, _ = _mihomo_request("GET", "/version", timeout=2)
    return code == 200


def _api_base() -> str:
    return load_config().api_base


def _http_proxy() -> str:
    return load_config().http_proxy


def _mihomo_request(method: str, path: str, body: dict | None = None, timeout: float = 10) -> tuple[int, Any]:
    url = f"{_api_base()}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return resp.status, {}
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {"message": exc.reason}
        except json.JSONDecodeError:
            payload = {"message": raw[:300] or exc.reason}
        return exc.code, payload
    except OSError as exc:
        return 0, {"message": str(exc)}


def _apply_node_to_selectors(node: str, main_group: str) -> list[str]:
    """将所选节点写入主策略组及包含该节点的常见 Selector（便于 IP 探测走所选出口）。"""
    if not node:
        return []
    code, data = _mihomo_request("GET", "/proxies", timeout=8)
    if code != 200 or not isinstance(data, dict):
        return []
    proxies = data.get("proxies") or {}
    synced: list[str] = []
    seen: set[str] = set()
    candidates = [main_group, * _IP_TEST_EXTRA_GROUPS] if main_group else list(_IP_TEST_EXTRA_GROUPS)
    for gname in candidates:
        if not gname or gname in seen:
            continue
        seen.add(gname)
        item = proxies.get(gname)
        if not item or item.get("type") != "Selector":
            continue
        options = item.get("all") or []
        if node not in options:
            continue
        path = f"/proxies/{urllib.parse.quote(gname, safe='')}"
        sc, _ = _mihomo_request("PUT", path, {"name": node}, timeout=5)
        if sc in (200, 204):
            synced.append(gname)
    return synced


def _fetch_ip(*, use_proxy: bool, timeout: float = 15) -> dict:
    last_err = ""
    for url in _IP_URLS:
        try:
            if use_proxy:
                proxy = _http_proxy()
                handlers = [
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                    urllib.request.HTTPHandler(),
                ]
                opener = urllib.request.build_opener(*handlers)
            else:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(url, headers={"User-Agent": "AI-Desk-Mihomo-Check/1.0"})
            with opener.open(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace").strip()
            if url.endswith("format=json"):
                ip = json.loads(text).get("ip", text)
            else:
                ip = text.split()[0] if text else ""
            if ip:
                return {"ok": True, "ip": ip, "source": url}
        except Exception as exc:
            last_err = str(exc)[:200]
    return {"ok": False, "message": last_err or "查询失败"}


def _is_private_ip(ip: str) -> bool:
    if ip.startswith("127.") or ip == "::1":
        return True
    parts = ip.split(".")
    if len(parts) == 4:
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            return False
        if a == 10:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
    return False


def _geo_label(ip: str, *, timeout: float = 8) -> dict:
    if not ip or _is_private_ip(ip):
        return {"label": "内网 / 本地", "country": "", "region": "", "city": "", "isp": ""}
    url = (
        f"http://ip-api.com/json/{urllib.parse.quote(ip)}"
        "?lang=zh-CN&fields=status,country,regionName,city,isp,query"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Desk-Mihomo-Check/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("status") != "success":
            return {"label": "归属地未知", "country": "", "region": "", "city": "", "isp": ""}
        country = (data.get("country") or "").strip()
        region = (data.get("regionName") or "").strip()
        city = (data.get("city") or "").strip()
        isp = (data.get("isp") or "").strip()
        parts = [p for p in (country, region, city) if p]
        label = " · ".join(parts) if parts else "归属地未知"
        if isp:
            short_isp = isp if len(isp) <= 28 else isp[:25] + "…"
            label = f"{label}（{short_isp}）"
        return {"label": label, "country": country, "region": region, "city": city, "isp": isp}
    except Exception as exc:
        return {"label": f"归属查询失败", "country": "", "region": "", "city": "", "isp": "", "geo_error": str(exc)[:120]}


def _enrich_ip_result(result: dict) -> dict:
    if not result.get("ok") or not result.get("ip"):
        return result
    result = {**result, **_geo_label(str(result["ip"]))}
    return result


@api_view(["GET"])
@permission_classes([StandaloneOrAdmin])
def mihomo_subscription_get(request):
    from .subscription import subscription_status
    return Response({"ok": True, **subscription_status()})


@api_view(["POST"])
@permission_classes([StandaloneOrAdmin])
def mihomo_subscription_save(request):
    from .subscription import SubscriptionError, save_subscription
    try:
        return Response({"ok": True, "message": "订阅设置已保存", **save_subscription(request.data)})
    except SubscriptionError as exc:
        return Response({"ok": False, "message": str(exc)}, status=400)
    except Exception:
        return Response({"ok": False, "message": "订阅设置保存失败，请检查文件权限"}, status=500)


@api_view(["POST"])
@permission_classes([StandaloneOrAdmin])
def mihomo_subscription_update(request):
    from .subscription import SubscriptionError, update_subscription
    try:
        return Response(update_subscription(_mihomo_request, request.data.get("id")))
    except SubscriptionError as exc:
        return Response({"ok": False, "message": str(exc)}, status=409)
    except Exception:
        return Response({"ok": False, "message": "订阅更新未完成，请检查配置文件和目录权限"}, status=500)


@api_view(["POST"])
@permission_classes([StandaloneOrAdmin])
def mihomo_subscription_activate(request):
    from .subscription import SubscriptionError, update_subscription
    try:
        profile_id = request.data.get("id")
        if not profile_id:
            raise SubscriptionError("请选择要启用的订阅")
        return Response(update_subscription(_mihomo_request, profile_id, activate=True))
    except SubscriptionError as exc:
        return Response({"ok": False, "message": str(exc)}, status=409)
    except Exception:
        return Response({"ok": False, "message": "订阅启用未完成，请检查配置文件和目录权限"}, status=500)


@api_view(["POST"])
@permission_classes([StandaloneOrAdmin])
def mihomo_subscription_delete(request):
    from .subscription import SubscriptionError, delete_subscription
    try:
        return Response({"ok": True, "message": "订阅已删除", **delete_subscription(request.data.get("id", ""))})
    except SubscriptionError as exc:
        return Response({"ok": False, "message": str(exc)}, status=409)


@api_view(["GET"])
def mihomo_status(request):
    code, data = _mihomo_request("GET", "/version")
    if code == 200 and isinstance(data, dict):
        return Response({"ok": True, "running": True, "version": data.get("version", ""), "meta": data})
    return Response({
        "ok": False,
        "running": False,
        "message": data.get("message", "无法连接 Mihomo API，可在本页点击「启动 Mihomo」或执行 ./devtools/mihomo/run.sh"),
    })


@api_view(["POST"])
def mihomo_start(request):
    """在本机后台执行 devtools/mihomo/run.sh（仅允许仓库内固定路径）。"""
    if _mihomo_running():
        code, data = _mihomo_request("GET", "/version")
        ver = data.get("version", "") if isinstance(data, dict) else ""
        return Response({"ok": True, "already_running": True, "message": "Mihomo 已在运行", "version": ver})

    root = load_config().project_root
    mihomo_dir = _mihomo_dir()
    try:
        mihomo_dir.relative_to(root)
    except ValueError:
        return Response({"ok": False, "message": "MIHOMO_DIR 配置无效"}, status=500)

    migrate_legacy_layout()
    run_sh = mihomo_dir / "run.sh"
    binary = bin_path()
    if not run_sh.is_file():
        return Response({"ok": False, "message": f"缺少启动脚本 {run_sh}"}, status=400)
    if not os.access(run_sh, os.X_OK):
        return Response({"ok": False, "message": f"{run_sh} 不可执行，请 chmod +x"}, status=400)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return Response({
            "ok": False,
            "message": "缺少 devtools/mihomo/bin/mihomo 二进制，请按 devtools/mihomo/README.md 下载解压",
        }, status=400)

    config_file = runtime_config_path()
    if not config_file.is_file():
        return Response({
            "ok": False,
            "message": f"缺少配置文件 {config_file}，请在管理页导入订阅或设置 MIHOMO_CONFIG",
        }, status=400)

    log_file = log_path()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as logf:
            logf.write(f"\n--- AI-Desk start {stamp} ---\n")
            logf.flush()
            proc = subprocess.Popen(
                ["/bin/bash", str(run_sh)],
                cwd=str(mihomo_dir),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        return Response({"ok": False, "message": f"启动失败: {exc}"}, status=500)

    for _ in range(40):
        if _mihomo_running():
            code, data = _mihomo_request("GET", "/version")
            ver = data.get("version", "") if isinstance(data, dict) and code == 200 else ""
            return Response({
                "ok": True,
                "message": "Mihomo 已启动",
                "version": ver,
                "pid": proc.pid,
            })
        time.sleep(0.5)

    if proc.poll() is not None:
        return Response({
            "ok": False,
            "message": f"进程已退出（code {proc.returncode}），请查看 devtools/mihomo/cache/mihomo.log",
        }, status=500)

    return Response({
        "ok": False,
        "message": "已发起启动，API 尚未就绪，请稍后点击刷新或查看 mihomo.log",
        "pid": proc.pid,
    }, status=202)


@api_view(["GET"])
def mihomo_proxies(request):
    code, data = _mihomo_request("GET", "/proxies")
    if code != 200 or not isinstance(data, dict):
        return Response({"ok": False, "message": data.get("message", "读取节点失败") if isinstance(data, dict) else "读取节点失败"})
    proxies = data.get("proxies") or {}
    main_group = MAIN_SELECTOR
    main = proxies.get(main_group) if isinstance(proxies, dict) else None
    if not main or main.get("type") != "Selector":
        choices = [(name, item) for name, item in proxies.items() if isinstance(item, dict) and item.get("type") == "Selector"]
        if not choices:
            return Response({"ok": False, "message": "未找到可手动选择的策略组"})
        main_group, main = next((choice for choice in choices if choice[0] != "GLOBAL"), choices[0])
    nodes = [n for n in main.get("all") or [] if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")]
    groups = []
    for name, item in proxies.items():
        if not isinstance(item, dict) or item.get("type") != "Selector":
            continue
        opts = [n for n in item.get("all") or [] if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")]
        groups.append({"name": name, "now": item.get("now", ""), "options": opts})
    groups.sort(key=lambda g: g["name"])
    return Response({
        "ok": True,
        "main_group": main_group,
        "current": main.get("now", ""),
        "nodes": nodes,
        "node_info": {
            name: {"type": (proxies.get(name) or {}).get("type", ""),
                   "udp": (proxies.get(name) or {}).get("udp")}
            for name in nodes
        },
        "groups": groups,
    })


@api_view(["POST"])
def mihomo_select(request):
    group = (request.data.get("group") or MAIN_SELECTOR).strip()
    node = (request.data.get("node") or "").strip()
    if not node:
        return Response({"ok": False, "message": "请选择节点"}, status=400)
    path = f"/proxies/{urllib.parse.quote(group, safe='')}"
    code, data = _mihomo_request("PUT", path, {"name": node})
    if code in (200, 204):
        return Response({"ok": True, "message": f"已切换 {group} → {node}", "group": group, "node": node})
    msg = data.get("message", "切换失败") if isinstance(data, dict) else "切换失败"
    return Response({"ok": False, "message": msg}, status=400)


def _measure_delay(name: str, *, timeout_ms: int, test_url: str) -> dict:
    q = urllib.parse.urlencode({"timeout": timeout_ms, "url": test_url})
    path = f"/proxies/{urllib.parse.quote(name, safe='')}/delay?{q}"
    code, data = _mihomo_request("GET", path, timeout=timeout_ms / 1000 + 8)
    if code == 200 and isinstance(data, dict) and "delay" in data:
        return {"name": name, "ok": True, "delay": int(data["delay"])}
    msg = data.get("message", "超时或不可达") if isinstance(data, dict) else "测速失败"
    return {"name": name, "ok": False, "delay": None, "message": msg}


def _main_group_nodes() -> tuple[list[str], str | None]:
    code, data = _mihomo_request("GET", "/proxies")
    if code != 200 or not isinstance(data, dict):
        return [], data.get("message", "读取节点失败") if isinstance(data, dict) else "读取节点失败"
    proxies = data.get("proxies") or {}
    main = proxies.get(MAIN_SELECTOR) if isinstance(proxies, dict) else None
    if not main:
        main = next((item for name, item in proxies.items() if name != "GLOBAL" and isinstance(item, dict) and item.get("type") == "Selector"), proxies.get("GLOBAL"))
        if not main:
            return [], "未找到可手动选择的策略组"
    nodes = [
        n for n in main.get("all") or []
        if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")
    ]
    return nodes, None


@api_view(["GET"])
def mihomo_delay(request):
    name = (request.GET.get("name") or "").strip()
    if not name:
        return Response({"ok": False, "message": "缺少 name"}, status=400)
    timeout = min(max(int(request.GET.get("timeout", 5000)), 1000), 15000)
    test_url = request.GET.get("url") or "http://www.gstatic.com/generate_204"
    result = _measure_delay(name, timeout_ms=timeout, test_url=test_url)
    if result.get("ok"):
        return Response({"ok": True, "name": name, "delay": result["delay"]})
    return Response({"ok": False, "message": result.get("message", "测速失败"), "name": name})


@api_view(["POST"])
def mihomo_delay_batch(request):
    """批量测延迟（Mihomo 标准 delay API，非带宽测速）。"""
    timeout = min(max(int(request.data.get("timeout", 5000)), 1000), 15000)
    test_url = (request.data.get("url") or "http://www.gstatic.com/generate_204").strip()
    workers = min(max(int(request.data.get("workers", 4)), 1), 8)

    names = request.data.get("names")
    if names is None:
        node_list, err = _main_group_nodes()
        if err:
            return Response({"ok": False, "message": err})
        names = node_list
    if not isinstance(names, list) or not names:
        return Response({"ok": False, "message": "无节点可测"}, status=400)

    names = [str(n).strip() for n in names if str(n).strip()][:80]
    results: list[dict] = [None] * len(names)  # type: ignore

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_measure_delay, name, timeout_ms=timeout, test_url=test_url): i
            for i, name in enumerate(names)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()

    items = [r for r in results if r is not None]
    items.sort(key=lambda x: (x.get("name") or ""))
    ok_n = sum(1 for x in items if x.get("ok"))
    return Response({
        "ok": True,
        "message": f"测速完成：{ok_n}/{len(items)} 可达",
        "items": items,
    })


@api_view(["GET"])
def mihomo_ip_check(request):
    node = (request.GET.get("node") or "").strip()
    group = (request.GET.get("group") or MAIN_SELECTOR).strip()
    synced_groups: list[str] = []
    if node:
        synced_groups = _apply_node_to_selectors(node, group)
    direct = _enrich_ip_result(_fetch_ip(use_proxy=False))
    proxy = _enrich_ip_result(_fetch_ip(use_proxy=True))
    message = "代理出口 IP 获取成功" if proxy.get("ok") else (proxy.get("message") or "代理 IP 获取失败")
    if proxy.get("ok") and node and synced_groups:
        message = f"已同步 {len(synced_groups)} 个策略组到「{node}」，{message}"
    return Response({
        "ok": proxy.get("ok", False),
        "direct": direct,
        "proxy": proxy,
        "node": node,
        "synced_groups": synced_groups,
        "message": message,
    })
