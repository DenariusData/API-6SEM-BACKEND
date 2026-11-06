"""
Usuário atual da requisição, para o log de auditoria (AKA-31).

O middleware lê o JWT do header Authorization e guarda o `sub` (uuid do
PerfilOperacional) num ContextVar enquanto a requisição roda. Assim os
signals de core_api/signals.py sabem quem fez a ação sem que cada view
precise repassar o usuário.

O middleware NÃO autentica nem barra nada: token ausente ou inválido só
deixa o usuário atual vazio. Quem decide se a requisição pode passar
continua sendo require_auth/require_role, na view.

ContextVar (e não variável global ou de thread) porque cada requisição,
inclusive em servidor assíncrono, enxerga só o próprio valor.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

import jwt

from core_api.utils.jwt_auth import decode_token

_usuario_atual = ContextVar("usuario_atual", default=None)


def get_usuario_atual():
    """uuid do usuário da requisição atual, ou None fora de requisição
    autenticada (comando de manutenção, login, token inválido)."""
    return _usuario_atual.get()


@contextmanager
def com_usuario(usuario_id):
    """Define o usuário atual fora de uma requisição (comando de manutenção,
    teste). Dentro de requisição quem define é o middleware."""
    marcador = _usuario_atual.set(usuario_id)
    try:
        yield
    finally:
        _usuario_atual.reset(marcador)


def _usuario_do_token(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        return uuid.UUID(str(decode_token(header[len("Bearer ") :].strip())["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


class UsuarioAtualMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        marcador = _usuario_atual.set(_usuario_do_token(request))
        try:
            return self.get_response(request)
        finally:
            _usuario_atual.reset(marcador)
