from django.urls import path

from . import views

urlpatterns = [
    path("mihomo/status/", views.mihomo_status),
    path("mihomo/start/", views.mihomo_start),
    path("mihomo/subscription/", views.mihomo_subscription_get),
    path("mihomo/subscription/save/", views.mihomo_subscription_save),
    path("mihomo/subscription/update/", views.mihomo_subscription_update),
    path("mihomo/subscription/activate/", views.mihomo_subscription_activate),
    path("mihomo/subscription/delete/", views.mihomo_subscription_delete),
    path("mihomo/proxies/", views.mihomo_proxies),
    path("mihomo/select/", views.mihomo_select),
    path("mihomo/delay/", views.mihomo_delay),
    path("mihomo/delay-batch/", views.mihomo_delay_batch),
    path("mihomo/ip-check/", views.mihomo_ip_check),
    path("mihomo/connectivity/", views.mihomo_connectivity),
]
