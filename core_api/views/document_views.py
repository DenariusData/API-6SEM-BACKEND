"""
Views relacionadas ao gerenciamento e upload de documentos.

    POST /api/documents/upload/   multipart/form-data com os 16 campos

Esta camada só faz HTTP: autentica, descobre o perfil de quem chamou pelo
token e traduz as exceções da service em status. A lista de campos e as
regras ficam em services/upload_documento.py.

Status: 201 criado (devolve o id) · 400 campo inválido ({"detail", "field"}) ·
401 sem token · 403 sem perfil operacional · 409 identificador + revisão já
cadastrados · 500 falha ao gravar.
"""

import logging
import uuid

from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.models import PerfilOperacional
from core_api.services import upload_documento
from core_api.utils.jwt_auth import require_auth

logger = logging.getLogger(__name__)


def _erro(detalhe, status, campo=None):
    corpo = {"detail": detalhe}
    if campo:
        corpo["field"] = campo
    return JsonResponse(corpo, status=status)


def _perfil_de(request):
    """PerfilOperacional de quem chamou (o `sub` do JWT é o `usuario_id`)."""
    try:
        return PerfilOperacional.objects.get(
            pk=uuid.UUID(str(request.auth_user["sub"]))
        )
    except (PerfilOperacional.DoesNotExist, ValueError, KeyError):
        return None


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def upload_document_view(request):
    """POST /api/documents/upload/"""
    # usuario_upload vem do token, nunca do formulário.
    perfil = _perfil_de(request)
    if perfil is None:
        return _erro("Perfil operacional do usuário não encontrado.", 403)

    try:
        documento = upload_documento.criar(
            request.POST, request.FILES.get("arquivo"), perfil
        )
    except upload_documento.DadosInvalidos as e:
        return _erro(str(e), 400, e.campo)
    except upload_documento.DocumentoDuplicado as e:
        return _erro(str(e), 409, "identificador_codigo")
    except OSError:
        logger.exception("Falha ao gravar o arquivo do upload")
        return _erro("Não foi possível salvar o arquivo no servidor.", 500)
    except DatabaseError:
        logger.exception("Falha ao gravar o documento do upload")
        return _erro("Não foi possível salvar o documento. Tente novamente.", 500)

    return JsonResponse(
        {
            "message": "Documento enviado para validação.",
            "id": str(documento.id),
            "status_validacao": documento.status_validacao,
        },
        status=201,
    )
