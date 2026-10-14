"""Mihomo 控制面与探测（无 Django 依赖）。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config

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


def _measure_delay(name: str, *, timeout_ms: int, test_url: str) -> dict:
    q = urllib.parse.urlencode({"timeout": timeout_ms, "url": test_url})
    path = f"/proxies/{urllib.parse.quote(name, safe='')}/delay?{q}"
    code, data = _mihomo_request("GET", path, timeout=timeout_ms / 1000 + 8)
    if code == 200 and isinstance(data, dict) and "delay" in data:
        return {"name": name, "ok": True, "delay": int(data["delay"])}
    msg = data.get("message", "超时或不可达") if isinstance(data, dict) else "测速失败"
    return {"name": name, "ok": False, "delay": None, "message": msg}


def _resolve_main_selector(proxies: dict) -> tuple[str, dict | None]:
    main_group = MAIN_SELECTOR
    main = proxies.get(main_group) if isinstance(proxies, dict) else None
    if not main or main.get("type") != "Selector":
        choices = [
            (name, item)
            for name, item in (proxies or {}).items()
            if isinstance(item, dict) and item.get("type") == "Selector"
        ]
        if not choices:
            return main_group, None
        main_group, main = next((c for c in choices if c[0] != "GLOBAL"), choices[0])
    return main_group, main if isinstance(main, dict) else None


def main_selector_state() -> tuple[str, str, list[str], str | None]:
    """主策略组、当前节点、可选节点列表；与 Web 验证 IP 一致。"""
    code, data = _mihomo_request("GET", "/proxies")
    if code != 200 or not isinstance(data, dict):
        err = data.get("message", "读取节点失败") if isinstance(data, dict) else "读取节点失败"
        return MAIN_SELECTOR, "", [], err
    proxies = data.get("proxies") or {}
    main_group, main = _resolve_main_selector(proxies)
    if not main:
        return main_group, "", [], "未找到可手动选择的策略组"
    nodes = [
        n for n in main.get("all") or []
        if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")
    ]
    return main_group, str(main.get("now") or ""), nodes, None


def _main_group_nodes() -> tuple[list[str], str | None]:
    _, _, nodes, err = main_selector_state()
    return nodes, err


def perform_ip_check(
    *,
    node: str = "",
    group: str | None = None,
    sync_current: bool = True,
) -> dict:
    """出口 IP：本机直连 + 代理（可选同步当前/指定节点到策略组）。"""
    main_group, current, _, err = main_selector_state()
    group = (group or main_group or MAIN_SELECTOR).strip()
    chosen = (node or "").strip()
    if sync_current and not chosen and current:
        chosen = current
    synced: list[str] = []
    if chosen:
        synced = _apply_node_to_selectors(chosen, group)
    direct = _enrich_ip_result(_fetch_ip(use_proxy=False))
    proxy = _enrich_ip_result(_fetch_ip(use_proxy=True))
    message = "代理出口 IP 获取成功" if proxy.get("ok") else (proxy.get("message") or "代理 IP 获取失败")
    if proxy.get("ok") and chosen and synced:
        message = f"已同步 {len(synced)} 个策略组到「{chosen}」，{message}"
    return {
        "ok": proxy.get("ok", False),
        "direct": direct,
        "proxy": proxy,
        "node": chosen,
        "group": group,
        "synced_groups": synced,
        "message": message,
        "selector_error": err,
    }


def print_ip_check(result: dict) -> None:
    if result.get("node"):
        print(f"同步节点: {result['node']}（{result.get('group', MAIN_SELECTOR)}）")
    if result.get("synced_groups"):
        print(f"已写入策略组: {', '.join(result['synced_groups'])}")
    direct = result.get("direct") or {}
    proxy = result.get("proxy") or {}
    d_ip = direct.get("ip") or direct.get("message", "—")
    p_ip = proxy.get("ip") or proxy.get("message", "—")
    print(f"本机直连: {d_ip}")
    if direct.get("label"):
        print(f"  归属: {direct['label']}")
    print(f"代理出口: {p_ip}  （经 http 代理，与 7890 一致）")
    if proxy.get("label"):
        print(f"  归属: {proxy['label']}")
    if result.get("message"):
        print(result["message"])

