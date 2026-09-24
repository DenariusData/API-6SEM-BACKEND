"""
View da tela de pergunta: recebe a pergunta, busca os trechos no acervo,
chama a IA e devolve a resposta com as fontes.
"""

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.models import RegistroPergunta
from core_api.services.rag.pergunta import ErroIA, responder_pergunta
from core_api.utils.jwt_auth import require_auth

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO_PERGUNTA = 1000


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
def pergunta_view(request):
    """
    Endpoint da tela de pergunta.
    POST /api/perguntas/  {"pergunta": "..."}
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "Dados de requisição inválidos."}, status=400)

    pergunta = str(data.get("pergunta", "")).strip()
    if not pergunta:
        return JsonResponse({"detail": "Informe a pergunta."}, status=400)
    if len(pergunta) > TAMANHO_MAXIMO_PERGUNTA:
        return JsonResponse(
            {
                "detail": "A pergunta deve ter no máximo "
                f"{TAMANHO_MAXIMO_PERGUNTA} caracteres."
            },
            status=400,
        )

    try:
        resultado = responder_pergunta(pergunta)
    except ErroIA:
        logger.exception("Falha ao responder a pergunta")
        return JsonResponse(
            {
                "detail": "O serviço de IA está indisponível no momento. "
                "Tente novamente mais tarde."
            },
            status=503,
        )

    tempos = resultado["tempos_ms"]
    RegistroPergunta.objects.create(
        usuario_id=request.auth_user.get("sub"),
        pergunta=pergunta,
        encontrou=resultado["encontrou"],
        quantidade_fontes=len(resultado["fontes"]),
        tempo_busca_ms=tempos["busca"],
        tempo_ia_ms=tempos["ia"],
        tempo_total_ms=tempos["total"],
    )
    logger.info(
        "Pergunta respondida em %d ms (busca %d ms, IA %d ms, encontrou=%s)",
        tempos["total"],
        tempos["busca"],
        tempos["ia"],
        resultado["encontrou"],
    )

    return JsonResponse({"pergunta": pergunta, **resultado}, status=200)
