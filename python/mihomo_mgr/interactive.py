"""Mihomo 交互式管理菜单。"""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from .cli import (
    cmd_delay_batch,
    cmd_select,
    cmd_sub_activate,
    cmd_sub_delete,
    cmd_sub_save,
    cmd_sub_update,
    cmd_verify_all,
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


def _pick_subscription() -> str | None:
    status = subscription_status()
    items = status.get("items") or []
    if not items:
        print("暂无订阅，请先导入。")
        return None
    print("\n订阅列表：")
    for i, row in enumerate(items, 1):
        mark = " [当前]" if row.get("active") else ""
        print(f"  {i}. {row.get('name', '?')}{mark}  id={row.get('id')}")
    raw = _prompt("选择序号或 id")
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            return str(items[idx]["id"])
        print("序号无效")
        return None
    return raw


def _pick_node(*, prompt: str = "选择节点序号或名称") -> str | None:
    code, data = _mihomo_request("GET", "/proxies")
    if code != 200:
        print("无法读取节点，请确认 Mihomo 已启动。")
        return None
    _, _, nodes, err = main_selector_state()
    if err:
        print(err)
        return None
    if not nodes:
        print("没有可选节点。")
        return None
    print("\n节点：")
    for i, name in enumerate(nodes, 1):
        print(f"  {i}. {name}")
    raw = _prompt(prompt)
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(nodes):
            return nodes[idx]
        print("序号无效")
        return None
    return raw


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
    if not items:
        print("暂无订阅。")
        return
    for row in items:
        mark = " [当前]" if row.get("active") else ""
        print(
            f"- {row.get('name', '?')}{mark}  "
            f"节点 {row.get('node_count') or '?'}  "
            f"更新 {row.get('updated_at') or '—'}  "
            f"id={row.get('id')}"
        )
        if row.get("url_display"):
            print(f"    {row['url_display']}")


def _action_sub_save() -> None:
    edit_id = _prompt("编辑已有订阅 id（留空表示新建）")
    name = _prompt("订阅名称")
    url = _prompt("完整 Clash/Mihomo 订阅 URL")
    via = _prompt_yes_no("经当前代理下载？", default=False)
    payload: dict = {"name": name, "url": url, "via_proxy": via}
    if edit_id:
        payload["id"] = edit_id
    cmd_sub_save(_ns(**payload))


def _action_sub_update() -> None:
    status = subscription_status()
    items = status.get("items") or []
    active = status.get("active_id", "")
    if items:
        print("\n订阅列表（留空 = 更新当前启用）：")
        for i, row in enumerate(items, 1):
            mark = " [当前]" if row.get("active") else ""
            print(f"  {i}. {row.get('name', '?')}{mark}  id={row.get('id')}")
    raw = _prompt("序号或 id", default="")
    sub_id = None
    if raw:
        if raw.isdigit() and items:
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                sub_id = str(items[idx]["id"])
            else:
                print("序号无效")
                return
        else:
            sub_id = raw
    elif not active:
        print("当前无启用订阅，请指定 id。")
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
    from .cli import cmd_nodes

    cmd_nodes(_ns())


def _action_select() -> None:
    node = _pick_node()
    if not node:
        return
    cmd_select(_ns(node=node, group=None))


def _action_delay_batch() -> None:
    workers_raw = _prompt("并发数 (1-8)", default="4")
    workers = int(workers_raw) if workers_raw.isdigit() else 4
    print("将对主策略组全部节点测延迟…")
    cmd_delay_batch(_ns(names=[], timeout=5000, url=None, workers=workers))


def _action_verify_all() -> None:
    """与 Web「一键验证」：出口 IP + 网站联通。"""
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
    ("2", "订阅列表", _action_sub_list),
    ("3", "导入 / 编辑订阅", _action_sub_save),
    ("4", "更新订阅", _action_sub_update),
    ("5", "启用订阅", _action_sub_activate),
    ("6", "删除订阅", _action_sub_delete),
    ("7", "节点列表", _action_nodes),
    ("8", "切换节点", _action_select),
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
