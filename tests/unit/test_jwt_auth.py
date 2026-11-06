import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from django.conf import settings
from django.http import JsonResponse
from django.test import RequestFactory

from core_api.utils.jwt_auth import (
    JWT_ALGORITHM,
    decode_token,
    generate_token,
    require_auth,
    require_role,
)

ENGENHARIA = {"id": "1", "email": "eng@akaer.com.br", "role_code": "ENGENHARIA"}
ADMIN = {"id": "2", "email": "admin@akaer.com.br", "role_code": "ADMINISTRADOR"}


def view(request):
    return JsonResponse({"email": request.auth_user["email"]})


def requisicao(token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return RequestFactory().get("/", headers=headers)


def test_token_gerado_pode_ser_decodificado():
    payload = decode_token(generate_token(ENGENHARIA))

    assert payload["sub"] == "1"
    assert payload["email"] == "eng@akaer.com.br"
    assert payload["role"] == "ENGENHARIA"


def test_token_assinado_com_outra_chave_e_recusado():
    token = jwt.encode({"sub": "1"}, "outra-chave", algorithm=JWT_ALGORITHM)

    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token)


def test_token_expirado_e_recusado():
    passado = datetime.now(timezone.utc) - timedelta(minutes=1)
    token = jwt.encode(
        {"sub": "1", "exp": passado}, settings.JWT_SECRET, algorithm=JWT_ALGORITHM
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


@pytest.mark.parametrize(
    "token, detalhe",
    [(None, "Token de autenticação ausente."), ("abc", "Token inválido.")],
)
def test_require_auth_recusa_sem_token_valido(token, detalhe):
    response = require_auth(view)(requisicao(token))

    assert response.status_code == 401
    assert json.loads(response.content)["detail"] == detalhe


def test_require_auth_libera_token_valido():
    response = require_auth(view)(requisicao(generate_token(ENGENHARIA)))

    assert response.status_code == 200
    assert json.loads(response.content)["email"] == "eng@akaer.com.br"


@pytest.mark.parametrize("usuario, status", [(ENGENHARIA, 403), (ADMIN, 200)])
def test_require_role_so_libera_papel_permitido(usuario, status):
    protegida = require_role("ADMINISTRADOR")(view)

    response = protegida(requisicao(generate_token(usuario)))

    assert response.status_code == status
