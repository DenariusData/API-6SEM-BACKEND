from core_api.views.auditoria_views import (
    exportar_logs_auditoria_view,
    logs_auditoria_view,
)
from core_api.views.auth_views import (
    confirmar_email_view,
    exportar_dados_view,
    login_view,
    profile_view,
    reenviar_confirmacao_view,
    user_detail_view,
    user_management_view,
)
from core_api.views.consulta_views import (
    acervo_view,
    detalhe_documento_view,
    meus_documentos_view,
    pendentes_view,
)
from core_api.views.document_views import reenviar_documento_view, upload_document_view
from core_api.views.opcoes_upload_views import (
    areas_view,
    idiomas_view,
    niveis_sigilo_view,
    responsaveis_view,
    tipos_documento_view,
)
from core_api.views.pergunta_views import (
    pergunta_view,
)
from core_api.views.termos_views import (
    aceite_termos_view,
    meus_termos_view,
    termos_admin_view,
    termos_pendentes_view,
)
from core_api.views.validacao_views import (
    aprovar_documento_view,
    devolver_documento_view,
)
from core_api.views.visualizacao_views import (
    pagina_documento_view,
    visualizar_documento_view,
)

__all__ = [
    "aceite_termos_view",
    "meus_termos_view",
    "termos_admin_view",
    "termos_pendentes_view",
    "user_detail_view",
    "login_view",
    "profile_view",
    "user_management_view",
    "confirmar_email_view",
    "exportar_dados_view",
    "reenviar_confirmacao_view",
    "upload_document_view",
    "reenviar_documento_view",
    "pergunta_view",
    "aprovar_documento_view",
    "devolver_documento_view",
    "pendentes_view",
    "meus_documentos_view",
    "acervo_view",
    "detalhe_documento_view",
    "tipos_documento_view",
    "areas_view",
    "niveis_sigilo_view",
    "idiomas_view",
    "responsaveis_view",
    "pagina_documento_view",
    "visualizar_documento_view",
    "logs_auditoria_view",
    "exportar_logs_auditoria_view",
]
