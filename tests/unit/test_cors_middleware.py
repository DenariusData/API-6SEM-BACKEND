from django.http import HttpResponse
from django.test import RequestFactory

from core_api.utils.cors_middleware import CorsMiddleware


def test_adiciona_cabecalhos_cors():
    middleware = CorsMiddleware(lambda request: HttpResponse())

    response = middleware(RequestFactory().get("/"))

    assert response["Access-Control-Allow-Origin"] == "*"
    assert "Authorization" in response["Access-Control-Allow-Headers"]


def test_preflight_options_responde_200():
    middleware = CorsMiddleware(lambda request: HttpResponse(status=405))

    response = middleware(RequestFactory().options("/"))

    assert response.status_code == 200
