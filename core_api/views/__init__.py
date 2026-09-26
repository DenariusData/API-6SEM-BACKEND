from core_api.views.auth_views import (
    login_view,
    profile_view,
    user_management_view,
)

from core_api.views.document_views import (
    upload_document_view,
)


__all__ = [
    "login_view",
    "profile_view",
    "user_management_view",
    "upload_document_view",
]