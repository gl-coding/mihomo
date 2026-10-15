"""Mihomo 运行时代理模式，与桌面系统代理开关相互独立。"""
from threading import Lock

from .service import _mihomo_request

MODES = ("rule", "global", "direct")
_LOCK = Lock()


class ProxyModeError(Exception):
    pass


def proxy_mode_status() -> dict:
    code, data = _mihomo_request("GET", "/configs", timeout=5)
    if code != 200 or not isinstance(data, dict):
        raise ProxyModeError("无法读取代理模式，请确认 Mihomo 正在运行")
    mode = str(data.get("mode", "")).lower()
    if mode not in MODES:
        raise ProxyModeError("内核返回了未知代理模式，请检查内核配置")
    tun = data.get("tun")
    result = {"mode": mode, "tun_enabled": isinstance(tun, dict) and tun.get("enable") is True,
              "global_current": "", "global_options": []}
    code, data = _mihomo_request("GET", "/proxies/GLOBAL", timeout=5)
    if code == 200 and isinstance(data, dict):
        result["global_current"] = data.get("now", "")
        result["global_options"] = [item for item in (data.get("all") or []) if isinstance(item, str)]
    return result


def set_proxy_mode(mode: str) -> dict:
    if mode not in MODES:
        raise ProxyModeError("代理模式仅支持规则、全局和直连")
    with _LOCK:
        code, _ = _mihomo_request("PATCH", "/configs", {"mode": mode}, timeout=5)
        if code not in (200, 204):
            raise ProxyModeError("代理模式切换失败，请确认 Mihomo 正在运行后重试")
        state = proxy_mode_status()
        if state["mode"] != mode:
            raise ProxyModeError("内核未应用所选代理模式，请刷新状态后重试")
        return state
