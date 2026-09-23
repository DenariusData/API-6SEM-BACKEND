"""
Views de Autenticacao SSO e Gestao de Usuarios da API.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.services.auth.autenticacao import (
    ERRO_USUARIO_BLOQUEADO,
    AuthService,
)
from core_api.utils.jwt_auth import generate_token, require_auth, require_role

UNIFIED_AUTH_ERROR_MSG = (
    "Usuário e/ou senha inválidos. Por favor, verifique suas credenciais de acesso."
)
BLOQUEADO_MSG = (
    "Conta temporariamente bloqueada por excesso de tentativas de login. "
    "Tente novamente mais tarde."
)


@csrf_exempt
@require_http_methods(["POST"])
def login_view(request):
    """
    Endpoint de Login SSO corporativo.
    POST /api/auth/login/
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"detail": UNIFIED_AUTH_ERROR_MSG},
            status=401,
        )

    email = data.get("email", "")
    password = data.get("password", "")

    if not email or not password:
        return JsonResponse(
            {"detail": UNIFIED_AUTH_ERROR_MSG},
            status=401,
        )

    user, erro = AuthService.authenticate(email, password)

    if not user:
        # Por razoes de seguranca, a mensagem generica nao revela se o
        # e-mail existe ou se apenas a senha foi errada. O unico caso em
        # que damos um retorno diferente e o de conta bloqueada, que ja e
        # informacao segura de se revelar (nao indica se a senha estava
        # certa) e ajuda o usuario a entender o que fazer.
        if erro == ERRO_USUARIO_BLOQUEADO:
            return JsonResponse({"detail": BLOQUEADO_MSG}, status=423)
        return JsonResponse(
            {"detail": UNIFIED_AUTH_ERROR_MSG},
            status=401,
        )

    token = generate_token(user)

    return JsonResponse(
        {
            "message": "Autenticação efetuada com sucesso!",
            "user": user,
            "token": token,
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["GET"])
@require_auth
def profile_view(request):
    """
    Endpoint para obter dados do perfil logado.
    GET /api/auth/me/

    A identidade vem do token (request.auth_user['sub']), nunca de um
    parametro de URL -- antes, ?user_id=<qualquer-id> permitia consultar
    o perfil de QUALQUER usuario sem nenhuma autenticacao.
    """
    user = AuthService.find_by_id(request.auth_user["sub"])
    if user:
        return JsonResponse({"user": user}, status=200)
    return JsonResponse({"detail": "Usuário não encontrado."}, status=404)


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_role("ADMINISTRADOR")
def user_management_view(request):
    """
    Endpoint de Gestao de Usuarios (Acessivel apenas para Administrador).
    GET /api/auth/users/ - Lista usuarios
    POST /api/auth/users/ - Cadastra novo usuario
    """
    if request.method == "GET":
        users = AuthService.get_all_users()
        return JsonResponse({"users": users, "count": len(users)}, status=200)

    elif request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"detail": "Dados de requisição inválidos."},
                status=400,
            )

        if not data.get("email") or not data.get("name"):
            return JsonResponse(
                {"detail": "Campos obrigatórios (email, nome) ausentes."},
                status=400,
            )

        new_user = AuthService.create_user(data)
        return JsonResponse(
            {
                "message": "Usuário cadastrado com sucesso!",
                "user": new_user,
            },
            status=201,
        )
