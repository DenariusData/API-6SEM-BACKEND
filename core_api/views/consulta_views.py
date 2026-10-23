"""
Endpoints de consulta do fluxo de validação (S2-35).

    GET /api/documents/pendentes/        fila de validação (só ADMINISTRADOR)
    GET /api/documents/meus/?status=     envios do usuário (qualquer autenticado),
                                         com `contagens` por status
    GET /api/documents/<id>/             metadados + histórico

As listas aceitam ?page= (20 por página). Esta camada só faz HTTP; as regras
(sem os próprios envios na fila, visibilidade do detalhe, nome sem e-mail)
ficam em services/consulta_documentos.py.

Status: 400 parâmetro inválido · 401 sem token · 403 não é validador (fila) ·
404 documento inexistente ou que o usuário não pode ver (detalhe).
"""

import uuid

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from core_api.models import Documento
from core_api.services import consulta_documentos as consulta
from core_api.utils.jwt_auth import require_auth, require_role
from credenciais.models import Papel


def _erro(detalhe, status, campo=None):
    corpo = {"detail": detalhe}
    if campo:
        corpo["field"] = campo
    return JsonResponse(corpo, status=status)


def _usuario_id(request):
    """uuid do usuário no JWT (é a mesma chave do PerfilOperacional)."""
    try:
        return uuid.UUID(str(request.auth_user["sub"]))
    except (KeyError, ValueError):
        return None


@require_http_methods(["GET"])
@require_role(Papel.ADMINISTRADOR)
def pendentes_view(request):
    """GET /api/documents/pendentes/"""
    usuario_id = _usuario_id(request)
    if usuario_id is None:
        return _erro("Token inválido.", 401)
    try:
        corpo = consulta.paginar(
            consulta.fila_de_pendentes(usuario_id),
            request.GET.get("page"),
            consulta.serializar_lista,
        )
    except consulta.ParametroInvalido as e:
        return _erro(str(e), 400, e.campo)
    return JsonResponse(corpo, status=200)


@require_http_methods(["GET"])
@require_auth
def meus_documentos_view(request):
    """GET /api/documents/meus/?status=DEVOLVIDO&page=1"""
    usuario_id = _usuario_id(request)
    if usuario_id is None:
        return _erro("Token inválido.", 401)
    try:
        queryset = consulta.meus_documentos(usuario_id, request.GET.get("status"))
        corpo = consulta.paginar(
            queryset,
            request.GET.get("page"),
            lambda itens: consulta.serializar_lista(itens, com_ultimo_comentario=True),
        )
    except consulta.ParametroInvalido as e:
        return _erro(str(e), 400, e.campo)
    corpo["contagens"] = consulta.contagens_por_status(usuario_id)
    return JsonResponse(corpo, status=200)


@require_http_methods(["GET"])
@require_auth
def detalhe_documento_view(request, documento_id):
    """GET /api/documents/<id>/"""
    try:
        corpo = consulta.detalhe(
            documento_id, _usuario_id(request), request.auth_user.get("role")
        )
    except Documento.DoesNotExist:
        return _erro("Documento não encontrado.", 404)
    return JsonResponse(corpo, status=200)