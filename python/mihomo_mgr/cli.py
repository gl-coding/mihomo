"""Mihomo 管理 CLI（与 Web/API 共用 mihomo_mgr 逻辑）。"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from .subscription import (
    SubscriptionError,
    delete_subscription,
    save_subscription,
    subscription_status,
    update_subscription,
)
from .service import (
    CONNECTIVITY_TARGETS,
    MAIN_SELECTOR,
    _check_connectivity,
    _main_group_nodes,
    _measure_delay,
    _mihomo_request,
    main_selector_state,
    perform_ip_check,
    print_ip_check,
)


def _emit(data: object, *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if isinstance(data, dict):
        if "message" in data and len(data) <= 4:
            print(data.get("message", data))
            return
        for key, val in data.items():
            if isinstance(val, (dict, list)):
                print(f"{key}:")
                _emit(val, json_mode=False)
            else:
                print(f"  {key}: {val}")
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                print("  " + " | ".join(f"{k}={v}" for k, v in row.items()))
            else:
                print(f"  {row}")
    else:
        print(data)


def _fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def cmd_status(args: argparse.Namespace) -> None:
    code, data = _mihomo_request("GET", "/version", timeout=2)
    if code == 200 and isinstance(data, dict):
        out = {"ok": True, "running": True, "version": data.get("version", ""), "meta": data}
    else:
        out = {
            "ok": False,
            "running": False,
            "message": data.get("message", "无法连接 Mihomo API") if isinstance(data, dict) else "无法连接",
        }
    _emit(out, json_mode=args.json)
    if not out.get("running"):
        raise SystemExit(1)


def cmd_sub_list(args: argparse.Namespace) -> None:
    out = {"ok": True, **subscription_status()}
    _emit(out, json_mode=args.json)


def cmd_sub_save(args: argparse.Namespace) -> None:
    payload: dict = {}
    if args.id:
        payload["id"] = args.id
    if args.name:
        payload["name"] = args.name
    if args.url:
        payload["url"] = args.url
    if args.via_proxy is not None:
        payload["via_proxy"] = args.via_proxy
    try:
        result = save_subscription(payload)
    except SubscriptionError as exc:
        _fail(str(exc))
    _emit({"ok": True, "message": "订阅设置已保存", **result}, json_mode=args.json)


def cmd_sub_update(args: argparse.Namespace) -> None:
    try:
        result = update_subscription(_mihomo_request, args.id)
    except SubscriptionError as exc:
        _fail(str(exc))
    _emit(result, json_mode=args.json)
    if not result.get("ok", True):
        raise SystemExit(1)


def cmd_sub_activate(args: argparse.Namespace) -> None:
    if not args.id:
        _fail("请指定订阅 id：sub activate <id>")
    try:
        result = update_subscription(_mihomo_request, args.id, activate=True)
    except SubscriptionError as exc:
        _fail(str(exc))
    _emit(result, json_mode=args.json)


def cmd_sub_delete(args: argparse.Namespace) -> None:
    if not args.id:
        _fail("请指定订阅 id：sub delete <id>")
    try:
        result = delete_subscription(args.id)
    except SubscriptionError as exc:
        _fail(str(exc))
    _emit({"ok": True, "message": "订阅已删除", **result}, json_mode=args.json)


def cmd_nodes(args: argparse.Namespace) -> None:
    code, data = _mihomo_request("GET", "/proxies")
    if code != 200 or not isinstance(data, dict):
        _fail(data.get("message", "读取节点失败") if isinstance(data, dict) else "读取节点失败")
    proxies = data.get("proxies") or {}
    main_group = MAIN_SELECTOR
    main = proxies.get(main_group)
    if not main or main.get("type") != "Selector":
        choices = [
            (name, item)
            for name, item in proxies.items()
            if isinstance(item, dict) and item.get("type") == "Selector"
        ]
        if not choices:
            _fail("未找到可手动选择的策略组")
        main_group, main = next((c for c in choices if c[0] != "GLOBAL"), choices[0])
    nodes = [n for n in main.get("all") or [] if n not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE")]
    out = {
        "ok": True,
        "main_group": main_group,
        "current": main.get("now", ""),
        "nodes": nodes,
    }
    if args.json:
        _emit(out, json_mode=True)
        return
    print(f"策略组: {main_group}  当前: {out['current']}")
    for name in nodes:
        mark = " *" if name == out["current"] else ""
        print(f"  {name}{mark}")


def cmd_select(args: argparse.Namespace) -> None:
    group = (args.group or MAIN_SELECTOR).strip()
    node = args.node.strip()
    path = f"/proxies/{urllib.parse.quote(group, safe='')}"
    code, data = _mihomo_request("PUT", path, {"name": node})
    if code in (200, 204):
        msg = f"已切换 {group} → {node}"
        _emit({"ok": True, "message": msg, "group": group, "node": node}, json_mode=args.json)
        return
    msg = data.get("message", "切换失败") if isinstance(data, dict) else "切换失败"
    _fail(msg)


def cmd_delay(args: argparse.Namespace) -> None:
    timeout = min(max(args.timeout, 1000), 15000)
    url = args.url or "http://www.gstatic.com/generate_204"
    result = _measure_delay(args.name, timeout_ms=timeout, test_url=url)
    _emit(result, json_mode=args.json)
    if not result.get("ok"):
        raise SystemExit(1)


def cmd_delay_batch(args: argparse.Namespace) -> None:
    timeout = min(max(args.timeout, 1000), 15000)
    url = args.url or "http://www.gstatic.com/generate_204"
    workers = min(max(args.workers, 1), 8)
    names = args.names
    if not names:
        names, err = _main_group_nodes()
        if err:
            _fail(err)
    names = [str(n).strip() for n in names if str(n).strip()][:80]
    results: list[dict | None] = [None] * len(names)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_measure_delay, name, timeout_ms=timeout, test_url=url): i
            for i, name in enumerate(names)
        }
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    items = [r for r in results if r is not None]
    ok_n = sum(1 for x in items if x.get("ok"))
    out = {"ok": True, "message": f"测速完成：{ok_n}/{len(items)} 可达", "items": items}
    if args.json:
        _emit(out, json_mode=True)
        return
    print(out["message"])
    for row in sorted(items, key=lambda x: x.get("name") or ""):
        if row.get("ok"):
            print(f"  {row['name']}: {row['delay']} ms")
        else:
            print(f"  {row['name']}: 失败 ({row.get('message', '')})")


def cmd_ip(args: argparse.Namespace) -> None:
    """验证出口 IP（默认同步主策略组当前节点，与 Web「验证 IP」一致）。"""
    out = perform_ip_check(
        node=(args.node or "").strip(),
        group=args.group,
        sync_current=not getattr(args, "no_sync", False),
    )
    if args.json:
        _emit(out, json_mode=True)
    else:
        print_ip_check(out)
    if not out.get("ok"):
        raise SystemExit(1)


def cmd_verify_all(args: argparse.Namespace) -> None:
    """一键验证：出口 IP + Google / ChatGPT / IPify 联通。"""
    out = perform_ip_check(
        node=(args.node or "").strip(),
        group=args.group,
        sync_current=not getattr(args, "no_sync", False),
    )
    targets = list(CONNECTIVITY_TARGETS)
    items = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item in pool.map(_check_connectivity, targets):
            items.append(item)
    conn = {"ok": True, "items": items}
    if args.json:
        _emit({"ok": out.get("ok", False), "ip": out, "connectivity": conn}, json_mode=True)
    else:
        print("── 出口 IP ──")
        print_ip_check(out)
        print("\n── 网站联通 ──")
        for item in items:
            state = item.get("state", "")
            status = item.get("http_status")
            extra = f" HTTP {status}" if status else ""
            ip_note = f" · 出口 {item['ip']}" if item.get("ip") else ""
            print(
                f"{item['name']}: {state}{extra} — {item.get('message', '')} "
                f"({item.get('elapsed_ms')} ms){ip_note}"
            )
    if not out.get("ok"):
        raise SystemExit(1)


def cmd_connectivity(args: argparse.Namespace) -> None:
    targets = args.targets
    if targets == ["all"]:
        targets = list(CONNECTIVITY_TARGETS)
    for t in targets:
        if t not in CONNECTIVITY_TARGETS:
            _fail(f"未知目标 {t!r}，可选: {', '.join(CONNECTIVITY_TARGETS)} 或 all")
    items = []
    with ThreadPoolExecutor(max_workers=min(len(targets), 3)) as pool:
        for item in pool.map(_check_connectivity, targets):
            items.append(item)
    out = {"ok": True, "items": items}
    if args.json:
        _emit(out, json_mode=True)
        return
    for item in items:
        state = item.get("state", "")
        status = item.get("http_status")
        extra = f" HTTP {status}" if status else ""
        print(f"{item['name']}: {state}{extra} — {item.get('message', '')} ({item.get('elapsed_ms')} ms)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mihomo", description="Mihomo 代理 CLI（订阅 / 节点 / 测速 / 连通性）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="查询 Mihomo 是否运行及版本")
    p_status.set_defaults(func=cmd_status)

    sub_sub = sub.add_parser("sub", help="订阅管理")
    sub_sub_sub = sub_sub.add_subparsers(dest="sub_cmd", required=True)

    p_sub_list = sub_sub_sub.add_parser("list", help="查看订阅列表与当前启用项")
    p_sub_list.set_defaults(func=cmd_sub_list)

    p_sub_save = sub_sub_sub.add_parser("save", help="导入或编辑订阅")
    p_sub_save.add_argument("--id", help="编辑已有订阅 id")
    p_sub_save.add_argument("--name", help="订阅名称")
    p_sub_save.add_argument("--url", help="完整 Clash/Mihomo 订阅 URL")
    p_sub_save.add_argument("--via-proxy", dest="via_proxy", action="store_true", help="经当前代理下载")
    p_sub_save.add_argument("--direct", dest="via_proxy", action="store_false", help="直连下载")
    p_sub_save.set_defaults(via_proxy=None, func=cmd_sub_save)

    p_sub_up = sub_sub_sub.add_parser("update", help="从机场更新订阅")
    p_sub_up.add_argument("id", nargs="?", default=None, help="订阅 id（默认当前启用）")
    p_sub_up.set_defaults(func=cmd_sub_update)

    p_sub_act = sub_sub_sub.add_parser("activate", help="启用指定订阅")
    p_sub_act.add_argument("id", help="订阅 id")
    p_sub_act.set_defaults(func=cmd_sub_activate)

    p_sub_del = sub_sub_sub.add_parser("delete", help="删除非当前订阅")
    p_sub_del.add_argument("id", help="订阅 id")
    p_sub_del.set_defaults(func=cmd_sub_delete)

    p_nodes = sub.add_parser("nodes", help="列出主策略组节点与当前选中")
    p_nodes.set_defaults(func=cmd_nodes)

    p_sel = sub.add_parser("select", help="切换节点")
    p_sel.add_argument("node", help="节点名称")
    p_sel.add_argument("--group", default=None, help=f"策略组（默认 {MAIN_SELECTOR}）")
    p_sel.set_defaults(func=cmd_select)

    p_delay = sub.add_parser("delay", help="单节点延迟测速")
    p_delay.add_argument("name", help="节点名称")
    p_delay.add_argument("--timeout", type=int, default=5000, help="超时毫秒（1000–15000）")
    p_delay.add_argument("--url", default=None, help="测速 URL")
    p_delay.set_defaults(func=cmd_delay)

    p_db = sub.add_parser("delay-batch", help="批量延迟测速（默认主策略组全部节点）")
    p_db.add_argument("names", nargs="*", help="指定节点名，省略则测主组全部")
    p_db.add_argument("--timeout", type=int, default=5000)
    p_db.add_argument("--url", default=None)
    p_db.add_argument("--workers", type=int, default=4)
    p_db.set_defaults(func=cmd_delay_batch)

    p_ip = sub.add_parser("ip", help="验证出口 IP（本机直连 + 代理出口）")
    p_ip.add_argument("--node", default="", help="指定节点（默认主策略组当前节点并同步）")
    p_ip.add_argument("--group", default=None, help="策略组")
    p_ip.add_argument("--no-sync", action="store_true", help="不同步节点到 GLOBAL 等组")
    p_ip.set_defaults(func=cmd_ip)

    p_va = sub.add_parser("verify-all", help="一键验证：出口 IP + 外网站点联通")
    p_va.add_argument("--node", default="")
    p_va.add_argument("--group", default=None)
    p_va.add_argument("--no-sync", action="store_true")
    p_va.set_defaults(func=cmd_verify_all)

    p_conn = sub.add_parser("connectivity", help="联通验证（仅 Google / ChatGPT / IPify，不经 IP 对比）")
    p_conn.add_argument(
        "targets",
        nargs="+",
        help=f"目标: {', '.join(CONNECTIVITY_TARGETS)} 或 all",
    )
    p_conn.set_defaults(func=cmd_connectivity)

    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("interactive", "menu", "i"):
        from .interactive import run_interactive

        run_interactive()
        return
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
