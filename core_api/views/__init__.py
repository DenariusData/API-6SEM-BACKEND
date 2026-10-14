from core_api.views.auth_views import (
    login_view,
    profile_view,
    user_management_view,
)
from core_api.views.consulta_views import (
    detalhe_documento_view,
    meus_documentos_view,
    pendentes_view,
)
from core_api.views.document_views import (
    upload_document_view,
)
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
from core_api.views.validacao_views import (
    aprovar_documento_view,
    devolver_documento_view,
)

__all__ = [
    "login_view",
    "profile_view",
    "user_management_view",
    "upload_document_view",
    "pergunta_view",
    "aprovar_documento_view",
    "devolver_documento_view",
    "pendentes_view",
    "meus_documentos_view",
    "detalhe_documento_view",
    "tipos_documento_view",
    "areas_view",
    "niveis_sigilo_view",
    "idiomas_view",
    "responsaveis_view",
]
