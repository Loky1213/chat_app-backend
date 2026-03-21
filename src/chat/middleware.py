from urllib.parse import parse_qs
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()


@database_sync_to_async
def get_user(user_id):
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


class JwtAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):

        query_string = scope.get("query_string", b"").decode("utf-8")
        query_params = parse_qs(query_string)
        token = query_params.get("token", [None])[0]

        print("🔍 TOKEN:", token)

        if token:
            try:
                access_token = AccessToken(token)

                user_id = access_token.get("user_id")
                print("👤 RAW USER ID:", user_id)

                if user_id is not None:
                    user_id = int(user_id)  # ✅ CRITICAL FIX

                user = await get_user(user_id)
                print("✅ AUTH USER:", user)

                scope["user"] = user

            except Exception as e:
                print("❌ JWT ERROR:", str(e))
                scope["user"] = AnonymousUser()
        else:
            print("❌ NO TOKEN")
            scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)

# from urllib.parse import parse_qs
# from channels.middleware import BaseMiddleware
# from channels.db import database_sync_to_async
# from django.contrib.auth import get_user_model
# from django.contrib.auth.models import AnonymousUser
# from rest_framework_simplejwt.tokens import AccessToken

# User = get_user_model()

# @database_sync_to_async
# def get_user(user_id):
#     try:
#         return User.objects.get(id=user_id)
#     except User.DoesNotExist:
#         return AnonymousUser()

# class JwtAuthMiddleware(BaseMiddleware):
#     async def __call__(self, scope, receive, send):
#         # 1. Extract token from query string (e.g., ?token=<JWT>)
#         query_string = scope.get("query_string", b"").decode("utf-8")
#         query_params = parse_qs(query_string)
#         token = query_params.get("token", [None])[0]

#         # 2. Decode token and fetch user
#         if token:
#             try:
#                 # AccessToken automatically validates expiration and signature
#                 access_token = AccessToken(token)
#                 user_id = access_token["user_id"]
                
#                 # Fetch user asynchronously and attach to scope
#                 scope["user"] = await get_user(user_id)
#             except Exception:
#                 # Invalid or expired token
#                 scope["user"] = AnonymousUser()
#         else:
#             # No token provided
#             scope["user"] = AnonymousUser()

#         # 3. Pass control to the next application (your consumer)
#         return await super().__call__(scope, receive, send)
