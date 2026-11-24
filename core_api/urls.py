"""
Configuração de URLs para a app core_api.
"""

from django.urls import path

from core_api.views import (
    aceite_termos_view,
    acervo_view,
    aprovar_documento_view,
    areas_view,
    confirmar_email_view,
    detalhe_documento_view,
    devolver_documento_view,
    exportar_dados_view,
    exportar_logs_auditoria_view,
    idiomas_view,
    login_view,
    logs_auditoria_view,
    meus_documentos_view,
    meus_termos_view,
    niveis_sigilo_view,
    pagina_documento_view,
    pendentes_view,
    pergunta_view,
    profile_view,
    reenviar_confirmacao_view,
    reenviar_documento_view,
    responsaveis_view,
    sugerir_tipo_view,
    termos_admin_view,
    termos_pendentes_view,
    tipos_documento_view,
    upload_document_view,
    user_detail_view,
    user_management_view,
    visualizar_documento_view,
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
        "auth/me/exportar/",
        exportar_dados_view,
        name="auth_me_exportar",
    ),
    path(
        "auth/confirmar-email/",
        confirmar_email_view,
        name="auth_confirmar_email",
    ),
    path(
        "auth/reenviar-confirmacao/",
        reenviar_confirmacao_view,
        name="auth_reenviar_confirmacao",
    ),
    # =====================================================
    # TERMOS E CONSENTIMENTO (AKA-109)
    # =====================================================
    path("termos/", termos_admin_view, name="termos_admin"),
    path("auth/termos/", meus_termos_view, name="auth_termos"),
    path("auth/termos-pendentes/", termos_pendentes_view, name="auth_termos_pendentes"),
    path("auth/termos/aceite/", aceite_termos_view, name="auth_termos_aceite"),
    path(
        "auth/users/",
        user_management_view,
        name="user_management",
    ),
    path(
        "auth/users/<uuid:usuario_id>/",
        user_detail_view,
        name="user_detail",
    ),
    # =====================================================
    # AUDITORIA (AKA-81, só ADMINISTRADOR)
    # =====================================================
    path(
        "auditoria/logs/",
        logs_auditoria_view,
        name="auditoria_logs",
    ),
    path(
        "auditoria/logs/exportar/",
        exportar_logs_auditoria_view,
        name="auditoria_logs_exportar",
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
    # OPÇÕES DO FORMULÁRIO DE UPLOAD
    # =====================================================
    path(
        "tipos-documento/",
        tipos_documento_view,
        name="tipos_documento",
    ),
    path(
        "areas/",
        areas_view,
        name="areas",
    ),
    path(
        "niveis-sigilo/",
        niveis_sigilo_view,
        name="niveis_sigilo",
    ),
    path(
        "idiomas/",
        idiomas_view,
        name="idiomas",
    ),
    path(
        "responsaveis/",
        responsaveis_view,
        name="responsaveis",
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
        "documents/sugerir-tipo/",
        sugerir_tipo_view,
        name="document_sugerir_tipo",
    ),
    path(
        "documents/pendentes/",
        pendentes_view,
        name="document_pendentes",
    ),
    path(
        "documents/meus/",
        meus_documentos_view,
        name="document_meus",
    ),
    path(
        "documents/disponiveis/",
        acervo_view,
        name="document_acervo",
    ),
    path(
        "documents/<uuid:documento_id>/",
        detalhe_documento_view,
        name="document_detalhe",
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
    path(
        "documents/<uuid:documento_id>/reenviar/",
        reenviar_documento_view,
        name="document_reenviar",
    ),
    path(
        "documents/<uuid:documento_id>/visualizar/",
        visualizar_documento_view,
        name="document_visualizar",
    ),
    path(
        "documents/<uuid:documento_id>/paginas/<int:numero>/",
        pagina_documento_view,
        name="document_pagina",
    ),
]
