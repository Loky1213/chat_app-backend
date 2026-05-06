from pathlib import Path
from datetime import timedelta
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# ==============================
# 🔐 SECURITY
# ==============================
SECRET_KEY = 'django-insecure-5t-0l%p$hyo*1p9g@*yo6m_y8spd1lpu&1x=-_fx&i+*pfz_-('

DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = ["*"]

AUTH_USER_MODEL = 'user.User'

# ==============================
# 🔧 APPS
# ==============================
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'channels',
    'channels_redis',
    'corsheaders',
    'rest_framework',
    'rest_framework.authtoken',
    'drf_spectacular',

    # Local apps
    'user',
    'chat',
]

# ==============================
# 🔧 MIDDLEWARE
# ==============================
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'src.urls'

# ==============================
# 🧠 TEMPLATES
# ==============================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ==============================
# ⚡ ASGI
# ==============================
ASGI_APPLICATION = "src.asgi.application"

# ==============================
# 🔴 REDIS (CHANNELS ONLY)
# ==============================
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

# ==============================
# 🗄 DATABASE
# ==============================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ==============================
# 🔐 PASSWORDS
# ==============================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ==============================
# 🌍 INTERNATIONALIZATION
# ==============================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ==============================
# 🌐 CORS
# ==============================
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://chat-app-frontend-ncgw-7ktd614lv.vercel.app",
]

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",
    "https://chat-app-frontend-ncgw-7ktd614lv.vercel.app",
]

# ==============================
# 📦 STATIC
# ==============================
STATIC_URL = 'static/'

# ==============================
# 🔌 DRF (SAFE VERSION)
# ==============================
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# ==============================
# 📄 API DOCS
# ==============================
SPECTACULAR_SETTINGS = {
    'TITLE': 'Chat App API',
    'DESCRIPTION': 'Chat Application Backend API',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ==============================
# 🔑 JWT
# ==============================
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=3),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

# ==============================
# 📊 LOGGING
# ==============================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "chat": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}