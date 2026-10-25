"""
apps.py

Django app configuration: mobile-first web interface.
"""

from django.apps import AppConfig


class WebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "web"
