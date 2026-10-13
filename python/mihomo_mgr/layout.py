"""devtools/mihomo 目录布局：bin / conf / cache。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import load_config

GEO_FILES = ("GeoSite.dat", "GeoIP.dat", "country.mmdb", "geoip.metadb")
RUNTIME_CONFIG = "active.yaml"


def mihomo_root() -> Path:
    return load_config().mihomo_dir


def bin_dir() -> Path:
    return mihomo_root() / "bin"


def bin_path() -> Path:
    candidate = bin_dir() / "mihomo"
    if candidate.is_file():
        return candidate
    legacy = mihomo_root() / "mihomo"
    return legacy if legacy.is_file() else candidate


def conf_dir() -> Path:
    return mihomo_root() / "conf"


def profiles_dir() -> Path:
    return conf_dir() / "profiles"


def geo_dir() -> Path:
    return conf_dir() / "geo"


def cache_dir() -> Path:
    return mihomo_root() / "cache"


def log_path() -> Path:
    legacy = mihomo_root() / "mihomo.log"
    current = cache_dir() / "mihomo.log"
    return current if current.is_file() or not legacy.is_file() else legacy


def subscription_lock_path() -> Path:
    return cache_dir() / ".subscription.lock"


def sync_runtime_config_for_api(source: Path | None = None) -> Path:
    """将运行配置同步到 cache/active.yaml，供 Mihomo API PUT /configs 重载（SAFE_PATHS 仅含 -d 目录）。"""
    ensure_directories()
    target = (cache_dir() / RUNTIME_CONFIG).resolve()
    src = source.resolve() if source else target
    if source and src != target and src.is_file():
        shutil.copy2(src, target)
        target.chmod(0o600)
    return target


def runtime_config_path() -> Path:
    root = mihomo_root()
    raw = os.environ.get("MIHOMO_CONFIG", "").strip()
    if raw:
        custom = Path(raw).resolve()
        if custom.is_file() and not custom.is_relative_to(cache_dir()):
            return sync_runtime_config_for_api(custom)
        return custom
    cache_cfg = cache_dir() / RUNTIME_CONFIG
    conf_cfg = conf_dir() / RUNTIME_CONFIG
    if conf_cfg.is_file():
        if not cache_cfg.is_file() or conf_cfg.stat().st_mtime > cache_cfg.stat().st_mtime:
            shutil.copy2(conf_cfg, cache_cfg)
            cache_cfg.chmod(0o600)
    if cache_cfg.is_file():
        return cache_cfg.resolve()
    for legacy in (root / "iKuuu_V2.yaml", conf_dir() / "iKuuu_V2.yaml"):
        if legacy.is_file():
            shutil.copy2(legacy, cache_cfg)
            cache_cfg.chmod(0o600)
            return cache_cfg.resolve()
    return cache_cfg.resolve()


def ensure_directories() -> None:
    for directory in (bin_dir(), conf_dir(), profiles_dir(), geo_dir(), cache_dir()):
        directory.mkdir(parents=True, exist_ok=True)


def ensure_geo_links() -> None:
    """Mihomo -d 使用 cache/；Geo 文件放在 conf/geo/ 并通过符号链接供内核读取。"""
    ensure_directories()
    work = cache_dir()
    geo = geo_dir()
    for name in GEO_FILES:
        source = geo / name
        if not source.is_file():
            flat = mihomo_root() / name
            if flat.is_file() and not source.exists():
                shutil.move(str(flat), str(source))
        if not source.is_file():
            continue
        target = work / name
        try:
            if target.is_symlink() and target.resolve() == source.resolve():
                continue
        except OSError:
            pass
        if target.exists() or target.is_symlink():
            target.unlink(missing_ok=True)
        target.symlink_to(source.resolve())


def migrate_legacy_layout() -> bool:
    """将旧版平铺目录迁移到 bin/conf/cache（幂等）。"""
    root = mihomo_root()
    changed = False
    ensure_directories()

    legacy_bin = root / "mihomo"
    dest_bin = bin_dir() / "mihomo"
    if legacy_bin.is_file() and not dest_bin.is_file():
        shutil.move(str(legacy_bin), str(dest_bin))
        dest_bin.chmod(0o755)
        changed = True

    for gz in root.glob("mihomo-*.gz"):
        dest = bin_dir() / gz.name
        if not dest.exists():
            shutil.move(str(gz), str(dest))
            changed = True

    for name in ("iKuuu_V2.yaml", "config.yaml"):
        src = root / name
        dest = conf_dir() / RUNTIME_CONFIG
        if src.is_file() and not dest.exists():
            shutil.move(str(src), str(dest))
            changed = True

    legacy_profiles = root / "profiles"
    if legacy_profiles.is_dir():
        for item in legacy_profiles.iterdir():
            dest = profiles_dir() / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
                changed = True
        try:
            legacy_profiles.rmdir()
        except OSError:
            pass

    for name in GEO_FILES:
        src = root / name
        dest = geo_dir() / name
        if src.is_file() and not dest.exists():
            shutil.move(str(src), str(dest))
            changed = True

    for name in ("cache.db", "mihomo.log"):
        src = root / name
        dest = cache_dir() / name
        if src.is_file() and not dest.exists():
            shutil.move(str(src), str(dest))
            changed = True

    lock = root / ".subscription.lock"
    lock_dest = cache_dir() / ".subscription.lock"
    if lock.is_file() and not lock_dest.exists():
        shutil.move(str(lock), str(lock_dest))
        changed = True

    for bak in root.glob("iKuuu_V2.yaml.*.bak"):
        dest = conf_dir() / bak.name.replace("iKuuu_V2.yaml", "active.yaml", 1)
        if not dest.exists():
            shutil.move(str(bak), str(dest))
            changed = True
    for bak in conf_dir().glob("iKuuu_V2.yaml.*.bak"):
        renamed = conf_dir() / bak.name.replace("iKuuu_V2.yaml", "active.yaml", 1)
        if not renamed.exists():
            bak.rename(renamed)
            changed = True

    conf_cfg = conf_dir() / RUNTIME_CONFIG
    cache_cfg = cache_dir() / RUNTIME_CONFIG
    if conf_cfg.is_file() and (
        not cache_cfg.is_file() or conf_cfg.stat().st_mtime >= cache_cfg.stat().st_mtime
    ):
        shutil.copy2(conf_cfg, cache_cfg)
        cache_cfg.chmod(0o600)
        changed = True

    ensure_geo_links()
    return changed


def assert_config_under_root(path: Path) -> Path:
    root = mihomo_root()
    if not path.is_relative_to(root):
        raise ValueError("订阅更新仅支持 devtools/mihomo 目录内的配置文件")
    return path
