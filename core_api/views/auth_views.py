"""
Views de Autenticação SSO e Gestão de Usuários da API.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.services.auth.autenticacao import AuthService

UNIFIED_AUTH_ERROR_MSG = (
    "Usuário e/ou senha inválidos. Por favor, verifique suas credenciais de acesso."
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

    user = AuthService.authenticate(email, password)

    if not user:
        # Por razões de segurança, retorna a mensagem unificada genérica sem revelar
        # se o e-mail existe ou se apenas a senha foi errada.
        return JsonResponse(
            {"detail": UNIFIED_AUTH_ERROR_MSG},
            status=401,
        )

    token = f"akaer-sso-token-{user['id']}-{user['role'].lower()}"

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
def profile_view(request):
    """
    Endpoint para obter dados do perfil logado.
    GET /api/auth/me/
    """
    user_id = request.GET.get("user_id")
    if user_id and user_id.isdigit():
        user = AuthService.find_by_id(int(user_id))
        if user:
            return JsonResponse({"user": user}, status=200)

    # Fallback para primeiro usuário de dev
    user = AuthService.find_by_id(1)
    return JsonResponse({"user": user}, status=200)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def user_management_view(request):
    """
    Endpoint de Gestão de Usuários (Acessível para Administrador).
    GET /api/auth/users/ - Lista usuários
    POST /api/auth/users/ - Cadastra novo usuário
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
