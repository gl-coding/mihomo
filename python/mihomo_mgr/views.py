"""Mihomo 外部控制 API 代理（供管理台选节点、测 IP）。"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .config import load_config
from .layout import bin_path, log_path, migrate_legacy_layout, runtime_config_path
from .permissions import StandaloneOrAdmin
from .service import (
    CONNECTIVITY_TARGETS,
    MAIN_SELECTOR,
    _check_connectivity,
    _main_group_nodes,
    _measure_delay,
    _mihomo_dir,
    _mihomo_request,
    _mihomo_running,
    main_selector_state,
    perform_ip_check,
)

__all__ = [
    "CONNECTIVITY_TARGETS",
    "MAIN_SELECTOR",
    "_check_connectivity",
    "_mihomo_request",
    "mihomo_connectivity",
    "mihomo_delay",
    "mihomo_delay_batch",
    "mihomo_ip_check",
    "mihomo_proxies",
    "mihomo_select",
    "mihomo_start",
    "mihomo_status",
    "mihomo_subscription_activate",
    "mihomo_subscription_delete",
    "mihomo_subscription_get",
    "mihomo_subscription_save",
    "mihomo_subscription_update",
]


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
    group = request.GET.get("group")
    if not node:
        main_group, current, _, _ = main_selector_state()
        node = current
        if group is None:
            group = main_group
    payload = perform_ip_check(node=node, group=group, sync_current=bool(node))
    return Response({
        "ok": payload["ok"],
        "direct": payload["direct"],
        "proxy": payload["proxy"],
        "node": payload["node"],
        "synced_groups": payload["synced_groups"],
        "message": payload["message"],
    })
