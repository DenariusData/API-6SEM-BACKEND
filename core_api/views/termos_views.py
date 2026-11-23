"""
Termos e consentimento (AKA-109).

    GET  /api/termos/                 todos os termos (só ADMINISTRADOR)
    POST /api/termos/                 cadastra um termo novo
    GET  /api/auth/termos/            termos ativos com a resposta do usuário
    GET  /api/auth/termos-pendentes/  termos que o usuário ainda não respondeu
    POST /api/auth/termos/aceite/     {respostas: [{termo_id, aceito}]}

As rotas do usuário funcionam mesmo com termo obrigatório pendente (são elas
que resolvem a pendência). As regras ficam em services/termos.py.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.services import termos
from core_api.utils.jwt_auth import require_auth, require_role, sem_exigir_termos


def _erro(detalhe, status, campo=None):
    corpo = {"detail": detalhe}
    if campo:
        corpo["field"] = campo
    return JsonResponse(corpo, status=status)


def _corpo(request):
    try:
        dados = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_role("ADMINISTRADOR")
@sem_exigir_termos
def termos_admin_view(request):
    if request.method == "GET":
        return JsonResponse(
            {"termos": [termos.serializar(t) for t in termos.todos()]}, status=200
        )
    dados = _corpo(request)
    if dados is None:
        return _erro("Dados de requisição inválidos.", 400)
    try:
        termo = termos.cadastrar(request.auth_user["sub"], dados)
    except termos.DadosInvalidos as e:
        return _erro(str(e), 400, e.campo)
    return JsonResponse({"termo": termos.serializar(termo)}, status=201)


@require_http_methods(["GET"])
@require_auth
@sem_exigir_termos
def meus_termos_view(request):
    lista = termos.do_usuario(request.auth_user["sub"])
    return JsonResponse(
        {"termos": [termos.serializar(t, com_resposta=True) for t in lista]}, status=200
    )


@require_http_methods(["GET"])
@require_auth
@sem_exigir_termos
def termos_pendentes_view(request):
    lista = termos.pendentes(request.auth_user["sub"])
    return JsonResponse({"termos": [termos.serializar(t) for t in lista]}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
@sem_exigir_termos
def aceite_termos_view(request):
    dados = _corpo(request)
    if dados is None:
        return _erro("Dados de requisição inválidos.", 400)
    try:
        restantes = termos.responder(request.auth_user["sub"], dados.get("respostas"))
    except termos.DadosInvalidos as e:
        return _erro(str(e), 400, e.campo)
    return JsonResponse(
        {
            "pendentes": [termos.serializar(t) for t in restantes],
            "termos_pendentes": bool(restantes),
        },
        status=200,
    )
