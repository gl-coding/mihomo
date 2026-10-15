import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ADMIN_DIR = REPO_ROOT / "admin"
DIST_DIR = ADMIN_DIR / "standalone" / "dist"
ASSETS_DIR = DIST_DIR / "assets"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "mihomo-standalone-dev-only")
DEBUG = os.environ.get("MIHOMO_ADMIN_DEBUG", "true").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("MIHOMO_ADMIN_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "mihomo_site.urls"
WSGI_APPLICATION = "mihomo_site.wsgi.application"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/assets/"
STATICFILES_DIRS = [ASSETS_DIR] if ASSETS_DIR.is_dir() else []

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

PROJECT_ROOT = REPO_ROOT
MIHOMO_DIR = REPO_ROOT
MIHOMO_API_BASE = os.environ.get("MIHOMO_API_BASE", "http://127.0.0.1:9090")
MIHOMO_HTTP_PROXY = os.environ.get("MIHOMO_HTTP_PROXY", "http://127.0.0.1:7890")
MIHOMO_STANDALONE = True

import sys

_PYTHON = REPO_ROOT / "python"
if str(_PYTHON) not in sys.path:
    sys.path.insert(0, str(_PYTHON))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
