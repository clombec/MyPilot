"""
urls.py

Root URL configuration: admin, REST API and web interface.
"""

from django.contrib import admin
from django.urls import include, path

from api.api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("", include("web.urls")),
]
