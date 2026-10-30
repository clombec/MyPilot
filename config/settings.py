"""
settings.py

Django settings for the ClabPilot project.
All secrets and machine-specific values come from environment variables,
optionally loaded from a local `.env` file (never versioned).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    """Read an environment variable, treating an empty value as missing.

    `.env.example` lists every variable with an empty value: an empty
    `DATABASE_PATH=` must fall back to the default, not to an empty path.

    Args:
        name: Environment variable name.
        default: Value used when the variable is missing or empty.

    Returns:
        The stripped value, or the default.
    """
    return os.environ.get(name, "").strip() or default


def _env_list(name: str, default: str = "") -> list[str]:
    """Split a comma or semicolon separated environment variable into a list.

    Args:
        name: Environment variable name.
        default: Value used when the variable is missing.

    Returns:
        List of non-empty, stripped items.
    """
    raw = _env(name, default)
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


SECRET_KEY = _env("DJANGO_SECRET_KEY", "change-me-in-production")
DEBUG = _env("DEBUG", "True") == "True"
ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "api",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite in WAL mode: the web process and the bot process share the same file.
# The live database must never sit in a cloud-synced folder.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(_env("DATABASE_PATH", str(BASE_DIR / "data" / "clabpilot.sqlite3"))),
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = _env("TIMEZONE", "Europe/Paris")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:home"
LOGOUT_REDIRECT_URL = "web:login"

# --- Project settings -------------------------------------------------------

API_TOKEN = _env("API_TOKEN")
EXPORT_DIRS = [Path(p) for p in _env_list("EXPORT_DIRS")]
EXPORT_KEEP_JSON = int(_env("EXPORT_KEEP_JSON", "30"))

# --- Logging ----------------------------------------------------------------

LOG_DIR = Path(_env("LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "clabpilot.log",
            "maxBytes": 2_000_000,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "default",
        },
    },
    "root": {"handlers": ["console", "file"], "level": _env("LOG_LEVEL", "INFO")},
}
