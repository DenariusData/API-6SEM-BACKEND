"""
Utilitários de JWT e decorators de autenticação/autorização
(core_api.utils.jwt_auth).

Substitui o token fake ("akaer-sso-token-<id>-<role>") por um JWT
assinado de verdade: qualquer alteração no payload (id, email, role)
invalida a assinatura, e o token expira sozinho depois de
JWT_EXP_MINUTES. Sem isso, qualquer pessoa que soubesse o formato do
token antigo conseguia construir um token válido pra outro usuário só
digitando o UUID dele.
"""

import functools
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.http import JsonResponse

JWT_ALGORITHM = "HS256"


def generate_token(user: dict) -> str:
    """Gera um JWT assinado a partir do dict de usuário já composto pelo AuthService."""
    agora = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "role": user["role"],
        "iat": agora,
        "exp": agora + timedelta(minutes=settings.JWT_EXP_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decodifica e valida assinatura/expiração. Levanta jwt.PyJWTError se inválido."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])


def _extrair_token(request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header[len("Bearer ") :].strip() or None


def _autenticar(request):
    """
    Retorna (payload, None) se o token do header Authorization for
    válido, ou (None, JsonResponse-de-erro) caso contrário.
    """
    token = _extrair_token(request)
    if not token:
        return None, JsonResponse(
            {"detail": "Token de autenticação ausente."}, status=401
        )
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        return None, JsonResponse({"detail": "Token expirado."}, status=401)
    except jwt.InvalidTokenError:
        return None, JsonResponse({"detail": "Token inválido."}, status=401)
    return payload, None


def require_auth(view_func):
    """
    Exige um JWT válido no header 'Authorization: Bearer <token>'.
    Em caso de sucesso, disponibiliza o payload decodificado em
    request.auth_user (com 'sub', 'email', 'role').
    """

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        payload, erro = _autenticar(request)
        if erro:
            return erro
        request.auth_user = payload
        return view_func(request, *args, **kwargs)

    return wrapper


def require_role(*papeis_permitidos):
    """
    Exige autenticação válida E que request.auth_user['role'] esteja
    entre os papéis permitidos. Uso:

        @require_role("ADMINISTRADOR")
        def minha_view(request): ...
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            payload, erro = _autenticar(request)
            if erro:
                return erro
            if payload.get("role") not in papeis_permitidos:
                return JsonResponse(
                    {"detail": "Você não tem permissão para acessar este recurso."},
                    status=403,
                )
            request.auth_user = payload
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
