from django.http import FileResponse, Http404
from django.urls import include, path, re_path
from django.views.static import serve as static_serve

from .settings import DIST_DIR

DIST_INDEX = DIST_DIR / "index.html"
DIST_ASSETS = DIST_DIR / "assets"


def spa_index(_request):
    if not DIST_INDEX.is_file():
        raise Http404("请先运行 admin/start.sh 构建管理页（缺少 standalone/dist/index.html）")
    return FileResponse(DIST_INDEX.open("rb"), content_type="text/html; charset=utf-8")


def assets_serve(request, path):
    return static_serve(request, path, document_root=str(DIST_ASSETS))


urlpatterns = [
    path("api/", include("mihomo_mgr.urls")),
]

if DIST_DIR.is_dir():
    urlpatterns += [
        re_path(r"^assets/(?P<path>.*)$", assets_serve),
        re_path(r"^.*$", spa_index),
    ]
