"""
Configuracion del Sistema de Gestion Omnicanal para Retail.

Equivalencia local de la arquitectura objetivo del informe (Azure):
- Azure App Service (Django)          -> servidor local (runserver)
- Azure Database for PostgreSQL       -> PostgreSQL local
- Azure Monitor / registros           -> archivo logs/omnicanal.log + tabla RegistroActividad
- Respaldos administrados             -> comando "respaldar_bd" (pg_dump)
- Procesos programados                -> comando "procesar_pendientes"
- SMTP externo                        -> backend de correo configurable (consola por defecto)
- PayPal Sandbox                      -> Adapter: modo simulado o PayPal Sandbox real
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(nombre, defecto=False):
    valor = os.getenv(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ("1", "true", "t", "si", "yes", "on")


def env_int(nombre, defecto):
    try:
        return int(os.getenv(nombre, defecto))
    except (TypeError, ValueError):
        return defecto


DEBUG = env_bool("DEBUG", True)
SECRET_KEY = os.getenv("SECRET_KEY", "clave-solo-para-desarrollo-local-cambiar")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

NOMBRE_TIENDA = os.getenv("NOMBRE_TIENDA", "Retail Omnicanal")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.SesionMaximaMiddleware",
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
                "core.context_processors.tienda",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Base de datos: PostgreSQL (RNF de persistencia y concurrencia) ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "omnicanal"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"

# --- Seguridad de sesion (parametros iniciales de diseno del informe) ---
SESSION_COOKIE_AGE = 30 * 60            # 30 minutos de inactividad
SESSION_SAVE_EVERY_REQUEST = True       # renueva el plazo con cada actividad
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESION_DURACION_MAXIMA_HORAS = 8        # reautenticacion obligatoria
LOGIN_MAX_INTENTOS = 5                  # intentos fallidos antes del bloqueo
LOGIN_BLOQUEO_MINUTOS = 15

# En produccion (DEBUG=False) las cookies solo viajan por HTTPS.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Reglas de negocio ---
RESERVA_MINUTOS = env_int("RESERVA_MINUTOS", 15)
NOTIFICACION_MAX_INTENTOS = 5

# --- Pagos (Adapter) ---
PAGO_PROVEEDOR = os.getenv("PAGO_PROVEEDOR", "simulado")
PAGO_MONEDA = os.getenv("PAGO_MONEDA", "CLP")
PAGO_CUENTA_RECEPTORA = os.getenv("PAGO_CUENTA_RECEPTORA", "comercio-sandbox@example.com")
SIMULACION_INCIERTO = os.getenv("SIMULACION_INCIERTO", "completada")
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MONEDA = os.getenv("PAYPAL_MONEDA", "USD")
PAYPAL_CLP_POR_UNIDAD = env_int("PAYPAL_CLP_POR_UNIDAD", 950)

# --- Correo (RF-12) ---
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-responder@retail-omnicanal.local")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)

# --- Respaldos ---
PG_BIN = os.getenv("PG_BIN", "")
BACKUPS_DIR = BASE_DIR / "backups"

# --- Registros de aplicacion (equivalente local a Azure Monitor) ---
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "estandar": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOGS_DIR / "omnicanal.log"),
            "maxBytes": 2_000_000,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "estandar",
        },
        "consola": {"class": "logging.StreamHandler", "formatter": "estandar"},
    },
    "loggers": {
        "core": {"handlers": ["archivo", "consola"], "level": "INFO"},
        "django.request": {"handlers": ["archivo"], "level": "WARNING", "propagate": True},
        "django.security": {"handlers": ["archivo"], "level": "WARNING", "propagate": True},
    },
}
