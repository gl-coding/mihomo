"""Mihomo 交互式管理菜单。"""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from .cli import (
    cmd_select,
    cmd_sub_activate,
    cmd_sub_delete,
    cmd_sub_save,
    cmd_sub_update,
    cmd_verify_all,
    print_numbered_delay_items,
    print_numbered_nodes,
    run_delay_batch_items,
)
from .config import load_config
from .service import (
    _mihomo_request,
    main_selector_state,
)
from .subscription import SubscriptionError, subscription_status


def _ns(**kwargs) -> SimpleNamespace:
    base = {"json": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _pause() -> None:
    try:
        input("\n按 Enter 继续…")
    except EOFError:
        print()
        raise SystemExit(0) from None


def _prompt(label: str, *, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        line = input(f"{label}{hint}: ").strip()
    except EOFError:
        print()
        raise SystemExit(0) from None
    return line if line else default


def _prompt_yes_no(label: str, *, default: bool = False) -> bool:
    default_ch = "Y/n" if default else "y/N"
    try:
        line = input(f"{label} ({default_ch}): ").strip().lower()
    except EOFError:
        print()
        raise SystemExit(0) from None
    if not line:
        return default
    return line in ("y", "yes", "是", "1")


def _run(action) -> None:
    try:
        action()
    except SystemExit:
        pass
    except SubscriptionError as exc:
        print(f"✗ {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n(已取消)")


def _resolve_node_choice(raw: str, names: list[str]) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(names):
            return names[idx]
        print(f"序号无效，请输入 1–{len(names)}。")
        return None
    if raw in names:
        return raw
    print("未找到该节点，请输入列表中的序号或完整节点名。")
    return None


def print_numbered_subscriptions(items: list[dict]) -> None:
    if not items:
        print("暂无订阅。")
        return
    width = len(str(len(items)))
    for i, row in enumerate(items, 1):
        mark = "  ← 当前" if row.get("active") else ""
        name = row.get("name") or "?"
        nodes = row.get("node_count") if row.get("node_count") is not None else "?"
        updated = (row.get("updated_at") or "—")[:19]
        print(f"  {i:>{width}}. {name}{mark}  节点 {nodes}  更新 {updated}")
        if row.get("url_display"):
            print(f"       {row['url_display']}")
    print(f"输入序号 1–{len(items)} 选择订阅。")


def _subscription_item_by_choice(raw: str, items: list[dict]) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            return items[idx]
        print(f"序号无效，请输入 1–{len(items)}。")
        return None
    found = next((row for row in items if str(row.get("id")) == raw), None)
    if found:
        return found
    print("未找到该订阅，请用序号或 id。")
    return None


def _pick_subscription(*, prompt: str = "序号或 id") -> str | None:
    status = subscription_status()
    items = status.get("items") or []
    if not items:
        print("暂无订阅，请先导入。")
        return None
    print()
    print_numbered_subscriptions(items)
    raw = _prompt(prompt)
    item = _subscription_item_by_choice(raw, items)
    return str(item["id"]) if item else None


def _edit_subscription_item(item: dict) -> None:
    print(f"\n编辑订阅：{item.get('name', '?')}  (id={item.get('id')})")
    default_name = item.get("name") or ""
    name = _prompt("订阅名称", default=default_name)
    if not name.strip():
        print("名称不能为空")
        return
    url = _prompt("订阅 URL（留空不修改）", default="")
    via = _prompt_yes_no("经当前代理下载？", default=bool(item.get("via_proxy")))
    payload: dict = {"id": item["id"], "name": name.strip(), "via_proxy": via}
    if url.strip():
        payload["url"] = url.strip()
    cmd_sub_save(_ns(**payload))


def _pick_node(*, prompt: str = "序号或节点名") -> str | None:
    main_group, current, nodes, err = main_selector_state()
    if err:
        print(err)
        return None
    if not nodes:
        print("没有可选节点。")
        return None
    print()
    print_numbered_nodes(main_group=main_group, current=current, nodes=nodes)
    raw = _prompt(prompt)
    return _resolve_node_choice(raw, nodes)


def _action_status() -> None:
    code, data = _mihomo_request("GET", "/version", timeout=2)
    if code == 200 and isinstance(data, dict):
        print(f"✓ 运行中  版本 {data.get('version', '?')}")
    else:
        msg = data.get("message", "无法连接 9090") if isinstance(data, dict) else "无法连接"
        print(f"✗ 未运行或 API 不可达（{msg}）")
        print("  可选：菜单 11 后台启动，或前台执行 ./run.sh")


def _action_sub_list() -> None:
    status = subscription_status()
    items = status.get("items") or []
    print_numbered_subscriptions(items)
    if not items:
        return
    raw = _prompt("输入序号编辑该订阅（留空返回）", default="")
    item = _subscription_item_by_choice(raw, items)
    if item:
        _edit_subscription_item(item)


def _action_sub_save() -> None:
    status = subscription_status()
    items = status.get("items") or []
    if items:
        print("已有订阅（输入序号可改为编辑该条，留空则新建）：")
        print_numbered_subscriptions(items)
    raw = _prompt("序号（留空=新建）", default="")
    item = _subscription_item_by_choice(raw, items) if raw.strip() else None
    if item:
        _edit_subscription_item(item)
        return
    name = _prompt("订阅名称")
    url = _prompt("完整 Clash/Mihomo 订阅 URL")
    via = _prompt_yes_no("经当前代理下载？", default=False)
    cmd_sub_save(_ns(name=name, url=url, via_proxy=via))


def _action_sub_update() -> None:
    status = subscription_status()
    items = status.get("items") or []
    active = status.get("active_id", "")
    if not items:
        print("暂无订阅。")
        return
    print("留空并回车 = 更新当前启用的订阅")
    print_numbered_subscriptions(items)
    raw = _prompt("序号或 id", default="")
    sub_id = None
    if raw.strip():
        item = _subscription_item_by_choice(raw, items)
        if not item:
            return
        sub_id = str(item["id"])
    elif not active:
        print("当前无启用订阅，请输入序号。")
        return
    cmd_sub_update(_ns(id=sub_id))


def _action_sub_activate() -> None:
    sub_id = _pick_subscription()
    if not sub_id:
        return
    cmd_sub_activate(_ns(id=sub_id))


def _action_sub_delete() -> None:
    sub_id = _pick_subscription()
    if not sub_id:
        return
    if not _prompt_yes_no(f"确认删除订阅 {sub_id}？", default=False):
        return
    cmd_sub_delete(_ns(id=sub_id))


def _action_nodes() -> None:
    main_group, current, nodes, err = main_selector_state()
    if err:
        print(err)
        return
    print_numbered_nodes(main_group=main_group, current=current, nodes=nodes)


def _action_select() -> None:
    node = _pick_node(prompt="要切换到的序号或节点名")
    if not node:
        return
    cmd_select(_ns(node=node, group=None))


def _action_delay_batch() -> None:
    workers_raw = _prompt("并发数 (1-8)", default="4")
    workers = int(workers_raw) if workers_raw.isdigit() else 4
    print("将对主策略组全部节点测延迟…")
    try:
        items = run_delay_batch_items(workers=workers)
    except SystemExit:
        return
    print()
    print_numbered_delay_items(items)
    if not items:
        return
    if not _prompt_yes_no("切换到某一节点？", default=True):
        return
    names = [str(r.get("name") or "") for r in items]
    raw = _prompt("序号或节点名")
    node = _resolve_node_choice(raw, names)
    if not node:
        return
    cmd_select(_ns(node=node, group=None))


def _action_verify_all() -> None:
    cmd_verify_all(_ns(node="", group=None, no_sync=False))


def _action_start_background() -> None:
    root = load_config().mihomo_dir
    run_sh = root / "run.sh"
    log = root / "cache" / "mihomo.log"
    if not run_sh.is_file():
        print(f"缺少 {run_sh}")
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            ["/bin/bash", str(run_sh), "--background"],
            cwd=str(root),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(f"已发起后台启动 (pid {proc.pid})，日志: {log}")


MENU = (
    ("1", "Mihomo 运行状态", _action_status),
    ("2", "订阅列表（序号编辑）", _action_sub_list),
    ("3", "新建 / 导入订阅", _action_sub_save),
    ("4", "更新订阅", _action_sub_update),
    ("5", "启用订阅", _action_sub_activate),
    ("6", "删除订阅", _action_sub_delete),
    ("7", "节点列表（带序号）", _action_nodes),
    ("8", "切换节点（输入序号）", _action_select),
    ("9", "批量延迟测速", _action_delay_batch),
    ("10", "一键验证（出口 IP + 联通）", _action_verify_all),
    ("11", "后台启动 Mihomo", _action_start_background),
    ("0", "退出", None),
)


def run_interactive() -> None:
    print("Mihomo 交互管理（Ctrl+C 退出）\n")
    while True:
        print("─" * 40)
        for key, label, _ in MENU:
            print(f"  {key:>2}. {label}")
        print("─" * 40)
        try:
            choice = input("请选择: ").strip()
        except EOFError:
            print()
            break
        if choice in ("0", "q", "quit", "exit"):
            break
        action = next((fn for key, _, fn in MENU if key == choice and fn), None)
        if action is None:
            print("无效选项，请重试。")
            continue
        print()
        _run(action)
        _pause()
