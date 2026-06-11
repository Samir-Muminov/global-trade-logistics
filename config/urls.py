from django.urls import path, include

urlpatterns = [
    path("api/v1/", include("apps.api.v1.urls", namespace="api_v1")),
    path("api/v1/", include("apps.users.urls", namespace="users")),
]