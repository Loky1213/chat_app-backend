import os
import django

# 1. Set settings FIRST
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.settings")

# 2. Setup Django BEFORE importing models, consumers, or middleware
django.setup()

# 3. Import Channels and Middleware
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from chat.middleware import JwtAuthMiddleware
import chat.routing

# 4. Define Application
application = ProtocolTypeRouter({
    # Django's HTTP response handler
    "http": get_asgi_application(),

    # WebSocket handler utilizing our custom JWT Middleware
    "websocket": JwtAuthMiddleware(
        URLRouter(
            chat.routing.websocket_urlpatterns
        )
    ),
})