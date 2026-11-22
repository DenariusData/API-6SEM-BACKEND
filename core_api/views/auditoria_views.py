"""
Consulta do log de auditoria (AKA-81, RF29). Só ADMINISTRADOR.

    GET /api/auditoria/logs/?usuario=&de=&ate=&acao=&page=
        lista paginada (20 por página), do mais recente para o mais antigo,
        com `acoes` (as opções do filtro de ação)
    GET /api/auditoria/logs/exportar/?usuario=&de=&ate=&acao=
        o resultado filtrado inteiro em CSV

Só leitura: não há rota para alterar ou apagar registro (o log é só de
inclusão, AKA-80). As regras ficam em services/consulta_auditoria.py.

Status: 400 parâmetro inválido · 401 sem token · 403 não é ADMINISTRADOR.
"""

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from core_api.services import consulta_auditoria
from core_api.services.consulta_documentos import ParametroInvalido
from core_api.utils.jwt_auth import require_role
from credenciais.models import Papel


def _erro(e):
    return JsonResponse({"detail": str(e), "field": e.campo}, status=400)


@require_http_methods(["GET"])
@require_role(Papel.ADMINISTRADOR)
def logs_auditoria_view(request):
    """GET /api/auditoria/logs/"""
    try:
        corpo = consulta_auditoria.listar(request.GET)
    except ParametroInvalido as e:
        return _erro(e)
    return JsonResponse(corpo, status=200)


@require_http_methods(["GET"])
@require_role(Papel.ADMINISTRADOR)
def exportar_logs_auditoria_view(request):
    """GET /api/auditoria/logs/exportar/"""
    try:
        conteudo, nome = consulta_auditoria.exportar_csv(request.GET)
    except ParametroInvalido as e:
        return _erro(e)
    resposta = HttpResponse(conteudo, content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = f'attachment; filename="{nome}"'
    resposta["Cache-Control"] = "no-store"
    return resposta
