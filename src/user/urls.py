from django.urls import path
from .views import (
    RegisterView,
    LoginView,
    TokenRefreshView,
    LogoutView,
    MeView,
    UpdateProfileView,
    UserListView,
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('login/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('me/', MeView.as_view(), name='me'),
    path('me/update/', UpdateProfileView.as_view(), name='update_profile'),
    path('users/', UserListView.as_view(), name='user_list'),
]
