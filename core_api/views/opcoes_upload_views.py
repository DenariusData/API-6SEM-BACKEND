"""
Opções dos selects do formulário de upload (S2-32). Todas exigem login.

    GET /api/tipos-documento/
    GET /api/areas/              com categorias e subcategorias
    GET /api/niveis-sigilo/
    GET /api/idiomas/
    GET /api/responsaveis/?area= só id e nome (LGPD)

Resposta: {"results": [...]}. As consultas ficam em
services/opcoes_upload.py.

Status: 400 parâmetro inválido · 401 sem token.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from core_api.services import opcoes_upload as opcoes
from core_api.services.consulta_documentos import ParametroInvalido
from core_api.utils.jwt_auth import require_auth


def _lista(itens):
    return JsonResponse({"results": itens}, status=200)


@require_http_methods(["GET"])
@require_auth
def tipos_documento_view(request):
    """GET /api/tipos-documento/"""
    return _lista(opcoes.tipos_documento())


@require_http_methods(["GET"])
@require_auth
def areas_view(request):
    """GET /api/areas/"""
    return _lista(opcoes.areas())


@require_http_methods(["GET"])
@require_auth
def niveis_sigilo_view(request):
    """GET /api/niveis-sigilo/"""
    return _lista(opcoes.niveis_sigilo())


@require_http_methods(["GET"])
@require_auth
def idiomas_view(request):
    """GET /api/idiomas/"""
    return _lista(opcoes.idiomas())


@require_http_methods(["GET"])
@require_auth
def responsaveis_view(request):
    """GET /api/responsaveis/?area=<id>"""
    try:
        return _lista(opcoes.responsaveis(request.GET.get("area")))
    except ParametroInvalido as e:
        return JsonResponse({"detail": str(e), "field": e.campo}, status=400)
