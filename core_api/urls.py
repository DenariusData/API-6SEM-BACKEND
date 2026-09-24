"""
Configuração de URLs para a app core_api.
"""

from django.urls import path

from core_api.views import (
    login_view,
    pergunta_view,
    profile_view,
    user_management_view,
)

urlpatterns = [
    path("auth/login/", login_view, name="auth_login"),
    path("auth/me/", profile_view, name="auth_me"),
    path("auth/users/", user_management_view, name="user_management"),
    path("perguntas/", pergunta_view, name="pergunta"),
]
