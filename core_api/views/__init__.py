from core_api.views.auth_views import (
    login_view,
    profile_view,
    user_management_view,
)
from core_api.views.document_views import (
    upload_document_view,
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
]
