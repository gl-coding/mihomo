from rest_framework.permissions import BasePermission, IsAdminUser

from .config import load_config


class StandaloneOrAdmin(BasePermission):
    """独立管理站默认仅监听本机；嵌入 nginxRouter 时订阅接口需管理员。"""

    def has_permission(self, request, view) -> bool:
        if load_config().standalone:
            return True
        return IsAdminUser().has_permission(request, view)
