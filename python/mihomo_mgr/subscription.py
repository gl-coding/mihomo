"""Manual subscription refresh with private settings and rollback."""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .layout import (
    assert_config_under_root,
    bin_path,
    cache_dir,
    ensure_geo_links,
    migrate_legacy_layout,
    profiles_dir,
    runtime_config_path,
    subscription_lock_path,
    sync_runtime_config_for_api,
)
from .config import load_config

MAX_DOWNLOAD = 5 * 1024 * 1024
MIHOMO_VALIDATE_TIMEOUT = int(os.environ.get("MIHOMO_VALIDATE_TIMEOUT", "120"))
LOCAL_KEYS = (
    "port", "socks-port", "mixed-port", "redir-port", "tproxy-port", "allow-lan",
    "bind-address", "external-controller", "external-controller-tls", "external-controller-unix",
    "external-controller-pipe", "secret", "authentication", "external-ui", "external-ui-url",
    "external-ui-name", "external-ui-upstream-url", "tun", "listeners", "interface-name", "routing-mark",
)


class SubscriptionError(Exception):
    pass


def config_path() -> Path:
    migrate_legacy_layout()
    try:
        return assert_config_under_root(runtime_config_path())
    except ValueError as exc:
        raise SubscriptionError(str(exc)) from exc


def _migrate_settings_file_if_needed() -> None:
    cfg = load_config()
    target = cfg.settings_path
    legacy = cfg.legacy_settings_path
    if target.is_file() or not legacy.is_file():
        return
    if legacy.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    target.chmod(0o600)


def _settings_path() -> Path:
    _migrate_settings_file_if_needed()
    path = load_config().settings_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_SETTING_KEYS = (
    "MIHOMO_SUBSCRIPTION_URL",
    "MIHOMO_SUBSCRIPTION_PROXY",
    "MIHOMO_SUBSCRIPTION_UPDATED_AT",
    "MIHOMO_SUBSCRIPTIONS_JSON",
    "MIHOMO_SUBSCRIPTION_ACTIVE_ID",
)
_SETTING_LINE = re.compile(
    r"^\s*(?:export\s+)?(" + "|".join(re.escape(key) for key in _SETTING_KEYS) + r")\s*=\s*(.*)$",
)


def _parse_env_value(key: str, raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        parsed = json.loads(raw)
        if key == "MIHOMO_SUBSCRIPTIONS_JSON":
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
            if isinstance(parsed, str):
                return parsed
            raise SubscriptionError("订阅列表格式无效")
        if isinstance(parsed, bool):
            return "true" if parsed else "false"
        return str(parsed)
    return raw.strip("'")


def _read_settings() -> dict:
    values = {key: "" for key in _SETTING_KEYS}
    path = _settings_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _SETTING_LINE.match(line)
            if match:
                values[match.group(1)] = _parse_env_value(match.group(1), match.group(2))
    else:
        # 不从 process 环境变量读取 JSON 列表（dotenv 易截断/双重编码）；仅兼容旧单订阅字段。
        for key in ("MIHOMO_SUBSCRIPTION_URL", "MIHOMO_SUBSCRIPTION_PROXY", "MIHOMO_SUBSCRIPTION_UPDATED_AT"):
            values[key] = os.environ.get(key, "")
    return values


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".subscription-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_settings(values: dict):
    path = _settings_path()
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(values)
    lines = [line for line in lines if not any(re.match(rf"^\s*(?:export\s+)?{key}\s*=", line) for key in keys)]
    for key, value in values.items():
        if key == "MIHOMO_SUBSCRIPTIONS_JSON" and isinstance(value, str) and value.strip():
            try:
                document = json.loads(value)
            except json.JSONDecodeError:
                document = value
            lines.append(f"{key}={json.dumps(document, ensure_ascii=False)}")
        else:
            lines.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    _atomic_write(path, ("\n".join(lines) + "\n").encode())


@contextmanager
def subscription_lock():
    migrate_legacy_layout()
    path = subscription_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SubscriptionError("订阅正在更新，请稍后再试") from exc
        yield
    finally:
        os.close(fd)


def validate_url(url: str):
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise SubscriptionError("订阅链接格式无效")
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        parsed.port
    except ValueError as exc:
        raise SubscriptionError("请输入有效的 HTTP / HTTPS 订阅链接") from exc


def subscription_status() -> dict:
    values = _read_settings()
    profiles, active_id = _profiles(values)
    migrate_profile_cache_files(profiles)
    active = next((item for item in profiles if item["id"] == active_id), {})
    url = active.get("url", "")
    parsed = urllib.parse.urlsplit(url)
    return {
        "configured": bool(url),
        "url_display": f"{parsed.scheme}://{parsed.hostname}/…" if url else "",
        "via_proxy": active.get("via_proxy", False),
        "updated_at": active.get("updated_at", ""),
        "active_id": active_id,
        "items": [_public_profile(item, active_id) for item in profiles],
    }


def _profiles(values: dict) -> tuple[list, str]:
    raw = values.get("MIHOMO_SUBSCRIPTIONS_JSON") or ""
    if raw:
        try:
            profiles = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(profiles, str):
                profiles = json.loads(profiles)
        except json.JSONDecodeError as exc:
            raise SubscriptionError(
                "订阅列表配置损坏，请编辑 devtools/mihomo/env/.env.mihomo.local 或重新添加订阅"
            ) from exc
        if isinstance(profiles, list):
            return profiles, values.get("MIHOMO_SUBSCRIPTION_ACTIVE_ID", "")
    url = values.get("MIHOMO_SUBSCRIPTION_URL", "")
    if not url:
        return [], ""
    return [{"id": "legacy", "name": "当前订阅", "url": url,
             "via_proxy": str(values["MIHOMO_SUBSCRIPTION_PROXY"]).lower() == "true",
             "updated_at": values["MIHOMO_SUBSCRIPTION_UPDATED_AT"], "node_count": None}], "legacy"


_TRAFFIC_PUBLIC_KEYS = (
    "traffic_upload", "traffic_download", "traffic_total", "expire_at", "profile_update_interval",
)


def _sanitize_profile_filename(name: str) -> str:
    base = re.sub(r'[/\\:*?"<>|\x00]+', "_", name.strip())
    base = re.sub(r"\s+", " ", base).strip(" .")
    return base or "subscription"


def _profile_cache_basename(item: dict, profiles: list) -> str:
    base = _sanitize_profile_filename(str(item.get("name") or item.get("id") or "subscription"))
    if len(base) > 80:
        base = base[:80].rstrip()
    others = {
        _sanitize_profile_filename(str(other.get("name") or other.get("id") or "subscription"))
        for other in profiles
        if other.get("id") != item.get("id")
    }
    if base in others:
        suffix = str(item.get("id") or "")[:8]
        base = f"{base}-{suffix}" if suffix else f"{base}-dup"
    return base


def _profile_cache_path(item: dict, profiles: list) -> Path:
    return profiles_dir() / f"{_profile_cache_basename(item, profiles)}.yaml"


def _legacy_profile_cache_paths(item: dict) -> list[Path]:
    root = profiles_dir()
    paths = [root / f"{item['id']}.yaml"]
    if item.get("id") == "legacy":
        paths.insert(0, root / "legacy.yaml")
    return paths


def migrate_profile_cache_files(profiles: list) -> None:
    """将旧版 {id}.yaml / legacy.yaml 重命名为与订阅名称一致的文件名。"""
    profiles_dir().mkdir(parents=True, exist_ok=True)
    for item in profiles:
        target = _profile_cache_path(item, profiles)
        for legacy in _legacy_profile_cache_paths(item):
            if not legacy.is_file() or legacy.resolve() == target.resolve():
                continue
            if not target.exists():
                legacy.rename(target)
            else:
                legacy.unlink(missing_ok=True)
            break


def _cache_path(profile_id: str) -> Path:
    if profile_id != "legacy" and not re.fullmatch(r"[a-f0-9]{32}", str(profile_id)):
        raise SubscriptionError("订阅标识无效")
    values = _read_settings()
    profiles, _ = _profiles(values)
    item = _find_profile(profiles, profile_id)
    migrate_profile_cache_files(profiles)
    return _profile_cache_path(item, profiles)


def _public_profile(item: dict, active_id: str) -> dict:
    url = item.get("url") or ""
    parsed = urllib.parse.urlsplit(url)
    return {key: item.get(key) for key in ("id", "name", "via_proxy", "updated_at", "node_count", *_TRAFFIC_PUBLIC_KEYS)} | {
        "url": url,
        "url_display": f"{parsed.scheme}://{parsed.hostname}/…" if url else "",
        "active": item["id"] == active_id,
        "has_cache": _cache_path(item["id"]).exists() or (item["id"] == "legacy" and bool(item.get("updated_at"))),
    }


def _store_profiles(values: dict, profiles: list, active_id: str):
    # Retain the old active profile before another subscription replaces the runtime file.
    for item in profiles:
        if item["id"] == "legacy" and item.get("updated_at") and not _cache_path("legacy").exists():
            _atomic_write(_cache_path("legacy"), config_path().read_bytes())
    active = next((item for item in profiles if item["id"] == active_id), {})
    values.update({"MIHOMO_SUBSCRIPTIONS_JSON": json.dumps(profiles, ensure_ascii=False),
                   "MIHOMO_SUBSCRIPTION_ACTIVE_ID": active_id,
                   "MIHOMO_SUBSCRIPTION_URL": active.get("url", ""),
                   "MIHOMO_SUBSCRIPTION_PROXY": "true" if active.get("via_proxy") else "false",
                   "MIHOMO_SUBSCRIPTION_UPDATED_AT": active.get("updated_at", "")})
    _write_settings(values)


def _find_profile(profiles: list, profile_id: str) -> dict:
    item = next((item for item in profiles if item["id"] == profile_id), None)
    if item is None:
        raise SubscriptionError("订阅不存在")
    return item


def save_subscription(payload: dict) -> dict:
    with subscription_lock():
        values = _read_settings()
        profiles, active_id = _profiles(values)
        invalidate_cache = False
        profile_id = payload.get("id")
        if profile_id:
            item = _find_profile(profiles, profile_id)
        elif "name" not in payload and profiles:
            item = _find_profile(profiles, active_id)
        else:
            profile_id = "legacy" if "name" not in payload and not profiles else uuid.uuid4().hex
            item = {"id": profile_id, "name": "当前订阅", "url": "", "via_proxy": False, "updated_at": "", "node_count": None}
            _clear_subscription_meta(item)
            profiles.append(item)
            if profile_id == "legacy":
                active_id = "legacy"
        prior_name = item.get("name")
        if "name" in payload:
            name = payload["name"].strip() if isinstance(payload["name"], str) else ""
            if not name or len(name) > 80:
                raise SubscriptionError("订阅名称必填且不超过 80 字")
            item["name"] = name
            if prior_name and prior_name != item["name"]:
                old_path = _profile_cache_path({**item, "name": prior_name}, profiles)
                new_path = _profile_cache_path(item, profiles)
                if old_path.is_file() and old_path != new_path:
                    if new_path.exists():
                        old_path.unlink(missing_ok=True)
                    else:
                        old_path.rename(new_path)
        if "url" in payload:
            url = payload["url"].strip() if isinstance(payload["url"], str) else ""
            if url:
                validate_url(url)
            if not url:
                raise SubscriptionError("订阅链接不能为空")
            if item["url"] and item["url"] != url:
                item["updated_at"] = ""
                item["node_count"] = None
                _clear_subscription_meta(item)
                invalidate_cache = True
            item["url"] = url
        if not item["url"]:
            raise SubscriptionError("请输入订阅链接")
        if "via_proxy" in payload:
            if not isinstance(payload["via_proxy"], bool):
                raise SubscriptionError("下载方式参数无效")
            item["via_proxy"] = payload["via_proxy"]
        _store_profiles(values, profiles, active_id)
        if invalidate_cache:
            _cache_path(item["id"]).unlink(missing_ok=True)
    return subscription_status()


def delete_subscription(profile_id: str) -> dict:
    with subscription_lock():
        values = _read_settings()
        profiles, active_id = _profiles(values)
        item = _find_profile(profiles, profile_id)
        if profile_id == active_id:
            raise SubscriptionError("当前订阅正在使用，请先启用另一个订阅")
        cache_file = _profile_cache_path(item, profiles)
        _store_profiles(values, [row for row in profiles if row["id"] != profile_id], active_id)
        cache_file.unlink(missing_ok=True)
    return subscription_status()


def _validation_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    proxy = load_config().http_proxy
    env["http_proxy"] = proxy
    env["https_proxy"] = proxy
    env["HTTP_PROXY"] = proxy
    env["HTTPS_PROXY"] = proxy
    return env


def _run_mihomo_config_test(config_file: Path, *, work_dir: Path) -> subprocess.CompletedProcess[str]:
    migrate_legacy_layout()
    ensure_geo_links()
    binary = bin_path()
    return subprocess.run(
        [str(binary), "-t", "-d", str(work_dir), "-f", str(config_file)],
        capture_output=True,
        timeout=MIHOMO_VALIDATE_TIMEOUT,
        env=_validation_subprocess_env(),
        text=True,
    )


def _subscription_meta_from_headers(headers) -> dict:
    """解析 Clash 订阅 HTTP 头（与 Clash Verge 等客户端同源）。

    常见 ``subscription-userinfo: upload=…; download=…; total=…; expire=…``（字节与 Unix 秒）。
    """
    normalized = {str(k).lower(): v for k, v in headers.items()}
    meta: dict = {}
    raw = normalized.get("subscription-userinfo", "")
    if raw:
        parsed: dict[str, int] = {}
        for segment in str(raw).split(";"):
            segment = segment.strip()
            if not segment or "=" not in segment:
                continue
            key, val = segment.split("=", 1)
            key, val = key.strip().lower(), val.strip()
            if key not in ("upload", "download", "total", "expire"):
                continue
            try:
                parsed[key] = int(val)
            except ValueError:
                continue
        if "upload" in parsed:
            meta["traffic_upload"] = parsed["upload"]
        if "download" in parsed:
            meta["traffic_download"] = parsed["download"]
        if "total" in parsed:
            meta["traffic_total"] = parsed["total"]
        if "expire" in parsed:
            meta["expire_at"] = parsed["expire"]
    interval = normalized.get("profile-update-interval", "")
    if str(interval).isdigit():
        meta["profile_update_interval"] = int(interval)
    return meta


def _apply_subscription_meta(item: dict, meta: dict) -> None:
    for key in ("traffic_upload", "traffic_download", "traffic_total", "expire_at", "profile_update_interval"):
        if key in meta:
            item[key] = meta[key]


def _clear_subscription_meta(item: dict) -> None:
    for key in ("traffic_upload", "traffic_download", "traffic_total", "expire_at", "profile_update_interval"):
        item.pop(key, None)


def _download(url: str, via_proxy: bool) -> tuple[bytes, dict]:
    validate_url(url)
    proxy = load_config().http_proxy
    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if via_proxy else {}), Redirects())
    req = urllib.request.Request(url, headers={"User-Agent": "clash-verge/AI-Desk"})
    try:
        with opener.open(req, timeout=15) as response:
            meta = _subscription_meta_from_headers(response.headers)
            deadline = time.monotonic() + 25
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise SubscriptionError("订阅下载超时，请稍后重试")
                chunk = response.read1(min(65536, MAX_DOWNLOAD + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD:
                    raise SubscriptionError("订阅内容超过 5MB 限制")
    except SubscriptionError:
        raise
    except urllib.error.HTTPError as exc:
        meta = _subscription_meta_from_headers(exc.headers)
        detail = f"HTTP {exc.code}"
        if meta:
            detail += "（响应含流量/到期头，但配置正文未下载成功）"
        exc.close()
        raise SubscriptionError(f"订阅下载失败：{detail}，请检查链接、Token 或下载方式") from exc
    except Exception as exc:
        # Network exceptions often include a private subscription URL.
        raise SubscriptionError("订阅下载失败，请检查链接和下载方式") from exc
    if not data or len(data) > MAX_DOWNLOAD:
        raise SubscriptionError("订阅内容为空或超过 5MB 限制")
    return bytes(data), meta


def _merge(downloaded: bytes, current: bytes) -> dict:
    try:
        remote = yaml.safe_load(downloaded)
        local = yaml.safe_load(current)
    except yaml.YAMLError as exc:
        raise SubscriptionError("订阅或本地配置不是有效的 YAML") from exc
    if not isinstance(remote, dict) or not isinstance(local, dict) or not remote.get("proxy-groups"):
        raise SubscriptionError("需要完整的 Clash / Mihomo YAML 订阅配置")
    if not remote.get("proxies") and not remote.get("proxy-providers"):
        raise SubscriptionError("订阅配置中没有代理节点")
    for key in LOCAL_KEYS:
        remote.pop(key, None)
        if key in local:
            remote[key] = local[key]
    remote.setdefault("allow-lan", False)
    remote.setdefault("bind-address", "127.0.0.1")
    remote.setdefault("external-controller", "127.0.0.1:9090")
    remote.pop("post-up", None)
    remote.pop("post-down", None)
    return remote


def _restore_runtime(api, path: Path, original: bytes, running: bool) -> str:
    _atomic_write(path, original)
    if not running:
        return "已恢复旧配置"
    try:
        reload_path = sync_runtime_config_for_api(path)
        code, _ = api("PUT", "/configs?force=true", {"path": str(reload_path)}, timeout=15)
    except Exception:
        code = 0
    return "已恢复旧配置" if code in (200, 204) else "旧文件已恢复，但代理运行状态需检查"


def update_subscription(api, profile_id: str | None = None, *, activate: bool = False) -> dict:
    with subscription_lock():
        values = _read_settings()
        profiles, active_id = _profiles(values)
        item = _find_profile(profiles, profile_id or active_id)
        path = config_path()
        original = path.read_bytes()
        # Migrate the existing profile cache before changing the active configuration.
        legacy = next((p for p in profiles if p["id"] == "legacy"), None)
        if active_id == "legacy" and legacy and legacy.get("updated_at") and not _cache_path("legacy").exists():
            _atomic_write(_cache_path("legacy"), original)
        migrate_profile_cache_files(profiles)
        cache = _profile_cache_path(item, profiles)
        downloaded = not activate or not cache.exists()
        download_meta: dict = {}
        if downloaded:
            raw, download_meta = _download(item["url"], item["via_proxy"])
        else:
            raw = cache.read_bytes()
        candidate = _merge(raw, original)
        fd, name = tempfile.mkstemp(prefix=".subscription-", suffix=".yaml", dir=path.parent)
        temporary = Path(name)
        applied = False
        running = False
        try:
            with os.fdopen(fd, "w") as stream:
                yaml.safe_dump(candidate, stream, allow_unicode=True, sort_keys=False)
            try:
                validation = _run_mihomo_config_test(temporary, work_dir=cache_dir())
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SubscriptionError(
                    f"Mihomo 配置校验超时（>{MIHOMO_VALIDATE_TIMEOUT}s），"
                    "若订阅含 GEOSITE 规则请先确保 Mihomo 已启动并在 devtools/mihomo/conf/geo 下存在 GeoSite.dat"
                ) from exc
            if validation.returncode:
                stderr = getattr(validation, "stderr", None) or ""
                stdout = getattr(validation, "stdout", None) or ""
                detail = (stderr or stdout).strip() if isinstance(stderr, str) else ""
                if "GeoSite" in detail or "geosite" in detail.lower():
                    raise SubscriptionError(
                        "Mihomo 校验失败：缺少 GeoSite.dat 或无法从 GitHub 下载。"
                        "请保持 Mihomo 运行并勾选「经代理下载」，或按 devtools/mihomo/README.md 预置 conf/geo/GeoSite.dat"
                    )
                tail = detail.splitlines()[-1][:200] if detail else "未知错误"
                raise SubscriptionError(f"Mihomo 配置校验失败，原配置已保留：{tail}")
            cached_data = temporary.read_bytes()
            should_apply = activate or item["id"] == active_id
            if should_apply:
                running = api("GET", "/version", timeout=3)[0] == 200
                old_nodes = api("GET", "/proxies", timeout=5)[1].get("proxies", {}) if running else {}
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                backup = path.with_name(path.name + f".{stamp}.bak")
                shutil.copy2(path, backup)
                backup.chmod(0o600)
                os.replace(temporary, path)
                applied = True
                if running:
                    reload_path = sync_runtime_config_for_api(path)
                    code, body = api("PUT", "/configs?force=true", {"path": str(reload_path)}, timeout=15)
                    if code not in (200, 204) or api("GET", "/version", timeout=3)[0] != 200:
                        hint = ""
                        if isinstance(body, dict) and body.get("message"):
                            hint = f"：{str(body['message'])[:160]}"
                        raise SubscriptionError(f"新配置重载失败（HTTP {code}{hint}）")
                    try:
                        code, new = api("GET", "/proxies", timeout=5)
                        if code == 200:
                            deadline = time.monotonic() + 10
                            for group, old in sorted(old_nodes.items(), key=lambda pair: pair[0] != "🔰 选择节点"):
                                if time.monotonic() >= deadline:
                                    break
                                selected = old.get("now")
                                if old.get("type") == "Selector" and selected in (new.get("proxies", {}).get(group, {}).get("all") or []):
                                    api("PUT", f"/proxies/{urllib.parse.quote(group, safe='')}", {"name": selected}, timeout=2)
                    except Exception:
                        pass  # Node selection is best-effort; the new config is already healthy.
            _atomic_write(cache, cached_data)
            if downloaded:
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                _apply_subscription_meta(item, download_meta)
            item["node_count"] = len(candidate.get("proxies") or [])
            if activate:
                active_id = item["id"]
            _store_profiles(values, profiles, active_id)
            message = "订阅已启用" if activate else "订阅已更新"
            if should_apply:
                message += "并重载" if running else "，启动 Mihomo 后生效"
            else:
                message += "，当前代理保持不变"
            return {"ok": True, "message": message, "node_count": item["node_count"], **subscription_status()}
        except Exception as exc:
            if applied:
                recovery = _restore_runtime(api, path, original, running)
                raise SubscriptionError(f"订阅操作失败，{recovery}") from exc
            raise
        finally:
            temporary.unlink(missing_ok=True)
