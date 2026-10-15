"""当前用户桌面系统代理；仅供本机独立管理站调用。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from threading import Lock
from urllib.parse import urlsplit

from .config import load_config

_LOCK = Lock()


class NetworkError(Exception):
    pass


def _run(*args: str) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError("无法执行桌面代理命令，请确认管理页在当前用户的桌面会话中启动") from exc
    if result.returncode or "dconf-WARNING" in result.stderr:
        raise NetworkError("系统代理读写失败，请检查桌面会话与配置文件权限")
    return result.stdout.strip()


def _tool(name: str) -> str:
    for candidate in (name + "6", name + "5", name):
        if path := shutil.which(candidate):
            return path
    raise NetworkError(f"缺少 {name}，请安装对应的 KDE 配置工具")


def _backend() -> str:
    if sys.platform != "linux":
        raise NetworkError("当前仅支持 Linux KDE Plasma 和 GNOME 桌面")
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")).lower()
    if "kde" in desktop or "plasma" in desktop:
        _tool("kwriteconfig")
        _tool("kreadconfig")
        return "KDE Plasma"
    if "gnome" in desktop or "unity" in desktop:
        if not shutil.which("gsettings"):
            raise NetworkError("缺少 gsettings，无法管理 GNOME 系统代理")
        return "GNOME"
    raise NetworkError("未识别到受支持的桌面，请从当前用户的 KDE 或 GNOME 桌面终端启动管理页")


def _target() -> tuple[str, int]:
    try:
        url = urlsplit(load_config().http_proxy)
        if url.scheme != "http" or not url.hostname or url.username or url.password:
            raise ValueError
        return url.hostname, url.port or 80
    except ValueError as exc:
        raise NetworkError("MIHOMO_HTTP_PROXY 必须是有效的 HTTP 代理地址") from exc


def _read(desktop: str) -> dict[str, str]:
    if desktop == "KDE Plasma":
        reader = _tool("kreadconfig")
        return {key: _run(reader, "--file", "kioslaverc", "--group", "Proxy Settings", "--key", key,
                          "--default", "0" if key == "ProxyType" else "")
                for key in ("ProxyType", "httpProxy", "httpsProxy", "socksProxy")}
    values = {"mode": _run("gsettings", "get", "org.gnome.system.proxy", "mode")}
    for protocol in ("http", "https", "socks"):
        for key in ("host", "port"):
            values[f"{protocol}.{key}"] = _run("gsettings", "get", f"org.gnome.system.proxy.{protocol}", key)
    return values


def _write(desktop: str, values: dict[str, str]) -> None:
    if desktop == "KDE Plasma":
        writer = _tool("kwriteconfig")
        for key, value in values.items():
            _run(writer, "--file", "kioslaverc", "--group", "Proxy Settings", "--key", key, value)
        if shutil.which("dbus-send"):
            _run("dbus-send", "--session", "--type=signal", "/KIO/Scheduler",
                 "org.kde.KIO.Scheduler.reparseSlaveConfiguration", "string:")
    else:
        for key, value in values.items():
            if key == "mode":
                _run("gsettings", "set", "org.gnome.system.proxy", key, value)
            else:
                protocol, field = key.split(".")
                _run("gsettings", "set", f"org.gnome.system.proxy.{protocol}", field, value)


def network_status() -> dict:
    try:
        desktop = _backend()
        values = _read(desktop)
        host, port = _target()
        address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        if desktop == "KDE Plasma":
            raw_mode = values["ProxyType"]
            mode = {"0": "off", "1": "system"}.get(raw_mode, "other")
            addresses = {protocol: values[f"{protocol}Proxy"] for protocol in ("http", "https", "socks")}
            matches = all(addresses[p] == f"{'socks' if p == 'socks' else 'http'}://{address}"
                          for p in addresses)
        else:
            raw_mode = values["mode"].strip("'")
            mode = {"none": "off", "manual": "system"}.get(raw_mode, "other")
            addresses = {p: f"{values[p + '.host'].strip(chr(39))}:{values[p + '.port']}"
                         for p in ("http", "https", "socks")}
            matches = all(values[p + ".host"].strip("'") == host and values[p + ".port"] == str(port)
                          for p in addresses)
        return {"supported": True, "desktop": desktop, "mode": mode, "raw_mode": raw_mode,
                "target": address, "addresses": addresses, "matches_target": matches,
                "message": ""}
    except NetworkError as exc:
        return {"supported": False, "desktop": "", "mode": "unknown", "target": "",
                "addresses": {}, "matches_target": False, "message": str(exc)}


def set_network_mode(mode: str) -> dict:
    if mode not in ("off", "system"):
        raise NetworkError("请选择关闭或系统代理")
    with _LOCK:
        desktop = _backend()
        before = _read(desktop)
        host, port = _target()
        address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        if desktop == "KDE Plasma":
            values = ({"httpProxy": f"http://{address}", "httpsProxy": f"http://{address}",
                       "socksProxy": f"socks://{address}", "ProxyType": "1"}
                      if mode == "system" else {"ProxyType": "0"})
        else:
            values = ({f"{p}.{key}": value for p in ("http", "https", "socks")
                       for key, value in (("host", repr(host)), ("port", str(port)))}
                      if mode == "system" else {})
            values["mode"] = "'manual'" if mode == "system" else "'none'"
        try:
            _write(desktop, values)
            state = network_status()
            if state["mode"] != mode or (mode == "system" and not state["matches_target"]):
                raise NetworkError("系统代理设置未生效")
        except NetworkError as exc:
            try:
                # 先恢复地址、最后恢复模式。
                mode_key = "ProxyType" if desktop == "KDE Plasma" else "mode"
                _write(desktop, {**{k: v for k, v in before.items() if k != mode_key}, mode_key: before[mode_key]})
            except NetworkError:
                raise NetworkError("设置失败且无法完整恢复，请在系统网络设置中检查代理") from exc
            raise NetworkError(f"{exc}，已恢复之前的设置") from exc
        return state
