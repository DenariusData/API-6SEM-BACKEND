"""
Configuração de URLs para a app core_api.
"""

from django.urls import path

from core_api.views import (
    aprovar_documento_view,
    devolver_documento_view,
    login_view,
    pergunta_view,
    profile_view,
    upload_document_view,
    user_management_view,
)

urlpatterns = [
    # =====================================================
    # AUTENTICAÇÃO
    # =====================================================
    path(
        "auth/login/",
        login_view,
        name="auth_login",
    ),
    path(
        "auth/me/",
        profile_view,
        name="auth_me",
    ),
    path(
        "auth/users/",
        user_management_view,
        name="user_management",
    ),
    # =====================================================
    # PERGUNTAS
    # =====================================================
    path(
        "perguntas/",
        pergunta_view,
        name="pergunta",
    ),
    # =====================================================
    # DOCUMENTOS
    # =====================================================
    path(
        "documents/upload/",
        upload_document_view,
        name="document_upload",
    ),
    path(
        "documents/<uuid:documento_id>/aprovar/",
        aprovar_documento_view,
        name="document_aprovar",
    ),
    path(
        "documents/<uuid:documento_id>/devolver/",
        devolver_documento_view,
        name="document_devolver",
    ),
]
