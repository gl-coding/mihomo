"""Mihomo 管理 API 配置：嵌入 nginxRouter 或独立 admin 站共用。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_env_cached: "MihomoConfig | None" = None


@dataclass(frozen=True)
class MihomoConfig:
    project_root: Path
    mihomo_dir: Path
    api_base: str
    http_proxy: str
    standalone: bool = False

    @property
    def settings_path(self) -> Path:
        return self.project_root / "env" / ".env.mihomo.local"


def _from_django() -> MihomoConfig | None:
    try:
        from django.conf import settings as dj

        if not getattr(dj, "configured", False):
            return None
        project_root = Path(getattr(dj, "PROJECT_ROOT", Path.cwd())).resolve()
        mihomo_dir = Path(
            getattr(dj, "MIHOMO_DIR", project_root / "devtools" / "mihomo")
        ).resolve()
        return MihomoConfig(
            project_root=project_root,
            mihomo_dir=mihomo_dir,
            api_base=str(getattr(dj, "MIHOMO_API_BASE", "http://127.0.0.1:9090")).rstrip("/"),
            http_proxy=str(getattr(dj, "MIHOMO_HTTP_PROXY", "http://127.0.0.1:7890")),
            standalone=bool(getattr(dj, "MIHOMO_STANDALONE", False)),
        )
    except ImportError:
        return None


def load_config() -> MihomoConfig:
    django_cfg = _from_django()
    if django_cfg is not None:
        return django_cfg

    global _env_cached
    if _env_cached is not None:
        return _env_cached

    repo_root = Path(__file__).resolve().parent.parent.parent
    mihomo_dir = Path(os.environ.get("MIHOMO_DIR", repo_root)).resolve()
    project_root = Path(os.environ.get("MIHOMO_PROJECT_ROOT", mihomo_dir)).resolve()
    _env_cached = MihomoConfig(
        project_root=project_root,
        mihomo_dir=mihomo_dir,
        api_base=os.environ.get("MIHOMO_API_BASE", "http://127.0.0.1:9090").rstrip("/"),
        http_proxy=os.environ.get("MIHOMO_HTTP_PROXY", "http://127.0.0.1:7890"),
        standalone=os.environ.get("MIHOMO_STANDALONE", "").lower() in ("1", "true", "yes"),
    )
    return _env_cached


def reset_config_cache() -> None:
    global _env_cached
    _env_cached = None
