"""
Views de Autenticacao SSO e Gestao de Usuarios da API.
"""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.services.auth import dados_pessoais
from core_api.services.auth.autenticacao import (
    ERRO_USUARIO_BLOQUEADO,
    AuthService,
)
from core_api.utils.jwt_auth import generate_token, require_auth, require_role
from credenciais.models import Usuario

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


def _erro(detalhe, status, campo=None):
    corpo = {"detail": detalhe}
    if campo:
        corpo["field"] = campo
    return JsonResponse(corpo, status=status)


def _json_objeto(request):
    """Corpo JSON que seja um objeto, ou None."""
    try:
        dados = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return dados if isinstance(dados, dict) else None


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_auth
def profile_view(request):
    """
    GET   /api/auth/me/  dados do perfil logado (inclui `email_pendente`).
    PATCH /api/auth/me/  {"nome", "email"}, ambos opcionais (S2-49).

    A identidade vem do token (request.auth_user['sub']), nunca de um
    parametro de URL ou do corpo -- antes, ?user_id=<qualquer-id> permitia
    consultar o perfil de QUALQUER usuario sem nenhuma autenticacao. No PATCH
    qualquer outra chave do corpo (papel, ativo, id...) e ignorada.

    PATCH: 200 · 400 campo invalido ou nada para alterar · 404 · 409 e-mail
    em uso · 429 mais de 1 link por minuto. Trocar o e-mail so grava
    `email_pendente` e envia o link; o e-mail muda em
    POST /api/auth/confirmar-email/.
    """
    if request.method == "PATCH":
        return _alterar_dados_pessoais(request)

    user = AuthService.find_by_id(request.auth_user["sub"])
    if user:
        return JsonResponse({"user": user}, status=200)
    return JsonResponse({"detail": "Usuário não encontrado."}, status=404)


def _alterar_dados_pessoais(request):
    dados = _json_objeto(request)
    if dados is None:
        return _erro("Dados de requisição inválidos.", 400)
    try:
        dados_pessoais.atualizar(request.auth_user["sub"], dados)
    except dados_pessoais.DadosInvalidos as e:
        return _erro(str(e), 400, e.campo)
    except dados_pessoais.EmailEmUso as e:
        return _erro(str(e), 409, "email")
    except dados_pessoais.MuitosPedidos as e:
        resposta = _erro(str(e), 429, "email")
        resposta["Retry-After"] = str(e.segundos)
        return resposta
    except (Usuario.DoesNotExist, ValueError):
        return _erro("Usuário não encontrado.", 404)

    corpo = {
        "message": "Dados atualizados.",
        "user": AuthService.find_by_id(request.auth_user["sub"]),
    }
    if corpo["user"]["email_pendente"]:
        corpo["message"] = (
            "Dados atualizados. Enviamos um link de confirmação para o novo "
            "e-mail; ele só vale depois de confirmado."
        )
    return JsonResponse(corpo, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def confirmar_email_view(request):
    """
    POST /api/auth/confirmar-email/ {"token"}

    Sem JWT de proposito: quem clica no link do e-mail pode nao estar logado
    neste navegador. O token (256 bits, uso unico, 24 h) e a credencial.
    400 token invalido ou expirado · 409 e-mail ocupado nesse meio tempo.
    """
    dados = _json_objeto(request)
    if dados is None:
        return _erro("Dados de requisição inválidos.", 400)
    try:
        dados_pessoais.confirmar_email(dados.get("token"))
    except dados_pessoais.TokenInvalido as e:
        return _erro(str(e), 400, "token")
    except dados_pessoais.EmailEmUso as e:
        return _erro(str(e), 409, "email")
    return JsonResponse({"message": "E-mail confirmado."}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
def reenviar_confirmacao_view(request):
    """
    POST /api/auth/reenviar-confirmacao/  (botao "Reenviar link", AKA-56)

    Gera token novo (o anterior deixa de valer) e reenvia o link.
    400 sem e-mail pendente · 429 mais de 1 pedido por minuto.
    """
    try:
        enviada = dados_pessoais.reenviar_confirmacao(request.auth_user["sub"])
    except dados_pessoais.SemEmailPendente as e:
        return _erro(str(e), 400)
    except dados_pessoais.MuitosPedidos as e:
        resposta = _erro(str(e), 429)
        resposta["Retry-After"] = str(e.segundos)
        return resposta
    except (Usuario.DoesNotExist, ValueError):
        return _erro("Usuário não encontrado.", 404)
    if not enviada:
        return _erro("Não foi possível enviar o e-mail agora. Tente novamente.", 502)
    return JsonResponse({"message": "Link de confirmação reenviado."}, status=200)


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
