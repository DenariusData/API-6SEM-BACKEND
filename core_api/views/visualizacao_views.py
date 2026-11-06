"""
Endpoints de visualização segura do documento (AKA-48 / S2-43).

    GET /api/documents/<id>/visualizar/        {total_paginas, titulo} + log
    GET /api/documents/<id>/paginas/<n>/       página n como PNG com marca d'água

O PDF original nunca sai do servidor: o navegador só recebe imagens.
As regras (arquivo, cache, marca, log) ficam em services/visualizacao.py.

Status: 401 sem token · 403 usuário sem perfil operacional ·
404 documento inexistente, sem permissão ou página fora do intervalo ·
415 documento que não é PDF.
"""

import uuid
from zoneinfo import ZoneInfo

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core_api.models import Documento
from core_api.services import visualizacao
from core_api.services.consulta_documentos import nomes_dos_usuarios, pode_ver
from core_api.utils.jwt_auth import require_auth

NAO_ENCONTRADO = "Documento não encontrado."
# Horário mostrado na marca d'água (o projeto guarda tudo em UTC).
FUSO_DA_MARCA = ZoneInfo("America/Sao_Paulo")


def _sem_cache(resposta):
    resposta["Cache-Control"] = "no-store"
    resposta["Pragma"] = "no-cache"
    resposta["X-Content-Type-Options"] = "nosniff"
    return resposta


def _erro(detalhe, status):
    return _sem_cache(JsonResponse({"detail": detalhe}, status=status))


def _usuario_id(request):
    try:
        return uuid.UUID(str(request.auth_user["sub"]))
    except (KeyError, ValueError):
        return None


def _documento_visivel(request, documento_id):
    """(documento, caminho_do_pdf, None) ou (None, None, resposta de erro).

    Inexistente e sem permissão dão o MESMO 404, para não revelar que o
    documento existe (mesma regra do detalhe, AKA-52)."""
    usuario_id = _usuario_id(request)
    if usuario_id is None:
        return None, None, _erro("Token inválido.", 401)
    documento = Documento.objects.filter(pk=documento_id).first()
    if documento is None or not pode_ver(
        documento, usuario_id, request.auth_user.get("role")
    ):
        return None, None, _erro(NAO_ENCONTRADO, 404)
    try:
        caminho = visualizacao.caminho_do_pdf(documento)
    except visualizacao.FormatoNaoSuportado as e:
        return None, None, _erro(str(e), 415)
    except visualizacao.ArquivoIndisponivel as e:
        return None, None, _erro(str(e), 404)
    return documento, caminho, None


@require_http_methods(["GET"])
@require_auth
def visualizar_documento_view(request, documento_id):
    """GET /api/documents/<id>/visualizar/ -> {total_paginas, titulo}.
    Grava VISUALIZACAO no LogAcesso, com o IP de origem."""
    documento, caminho, erro = _documento_visivel(request, documento_id)
    if erro:
        return erro
    if not visualizacao.registrar_visualizacao(
        documento, _usuario_id(request), visualizacao.ip_de(request)
    ):
        return _erro("Perfil operacional não encontrado para este usuário.", 403)
    return _sem_cache(
        JsonResponse(
            {
                "total_paginas": visualizacao.total_paginas(caminho),
                "titulo": documento.titulo,
            },
            status=200,
        )
    )


@require_http_methods(["GET"])
@require_auth
def pagina_documento_view(request, documento_id, numero):
    """GET /api/documents/<id>/paginas/<n>/ -> image/png com marca d'água.
    Nome e e-mail da marca vêm do usuário do token, nunca do front."""
    documento, caminho, erro = _documento_visivel(request, documento_id)
    if erro:
        return erro
    if not 1 <= numero <= visualizacao.total_paginas(caminho):
        return _erro("Página não encontrada.", 404)

    usuario_id = _usuario_id(request)
    nome = nomes_dos_usuarios([usuario_id]).get(usuario_id)
    texto = visualizacao.texto_da_marca(
        nome,
        request.auth_user.get("email"),
        timezone.localtime(timezone.now(), FUSO_DA_MARCA),
        documento.identificador_codigo,
    )
    png = visualizacao.pagina_com_marca(caminho, numero, texto)

    resposta = HttpResponse(png, content_type="image/png")
    resposta["Content-Disposition"] = f'inline; filename="pagina-{numero}.png"'
    return _sem_cache(resposta)
