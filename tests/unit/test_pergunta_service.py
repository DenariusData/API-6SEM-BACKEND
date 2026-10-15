from unittest.mock import Mock, patch

import pytest
import requests

from core_api.services.rag import pergunta
from core_api.services.rag.pergunta import (
    ErroIA,
    formatar_paginas,
    responder_pergunta,
)
from core_api.services.rag.responder import FRASES_NAO_ENCONTROU


def trecho(similaridade):
    return {
        "documento": "FED-STD/FED-STD-123H.020573.pdf",
        "titulo": "FED-STD-123H, FEDERAL STANDARD: MARKING FOR SHIPMENT",
        "url_pdf": "",
        "pagina_inicio": 7,
        "pagina_fim": 8,
        "texto": "Marking shall be ...",
        "similaridade": similaridade,
    }


def responder(trechos, resposta_ia="Resposta [1]", idioma="pt-BR"):
    with (
        patch.object(pergunta, "buscar_trechos", return_value=trechos),
        patch.object(pergunta, "perguntar_ia", return_value=resposta_ia) as ia,
    ):
        return responder_pergunta("Como marcar a carga?", idioma=idioma), ia


@pytest.mark.parametrize("p1, p2, esperado", [(7, 7, "7"), (42, 43, "42-43")])
def test_formatar_paginas(p1, p2, esperado):
    assert formatar_paginas(p1, p2) == esperado


def test_descarta_trechos_abaixo_da_similaridade_minima():
    resultado, ia = responder([trecho(0.9), trecho(0.1)])

    assert len(ia.call_args.args[1]) == 1
    assert resultado["encontrou"] is True
    assert len(resultado["fontes"]) == 1


def test_monta_fonte_com_documento_pagina_e_citacao():
    resultado, _ = responder([trecho(0.9)])

    fonte = resultado["fontes"][0]
    assert fonte["documento"] == "FED-STD-123H"
    assert fonte["revisao"] == "H"
    assert fonte["pagina"] == "7-8"
    assert fonte["url_pdf"] is None
    assert fonte["citada"] is True


@pytest.mark.parametrize("idioma", ["pt-BR", "en"])
def test_sem_trecho_relevante_nao_chama_a_ia(idioma):
    resultado, ia = responder([trecho(0.1)], idioma=idioma)

    ia.assert_not_called()
    assert resultado["encontrou"] is False
    assert resultado["resposta"] == FRASES_NAO_ENCONTROU[idioma]
    assert resultado["fontes"] == []


def test_ia_que_nao_acha_nos_trechos_nao_devolve_fontes():
    resultado, _ = responder([trecho(0.9)], resposta_ia=FRASES_NAO_ENCONTROU["pt-BR"])

    assert resultado["encontrou"] is False
    assert resultado["fontes"] == []


def test_ollama_fora_do_ar_vira_erro_ia():
    with patch.object(pergunta.requests, "post", side_effect=requests.ConnectionError):
        with pytest.raises(ErroIA, match="Não consegui conectar"):
            pergunta._ollama("/api/chat", {})


def test_ollama_com_erro_http_vira_erro_ia():
    falha = Mock(status_code=500, text="boom")
    with patch.object(pergunta.requests, "post", return_value=falha):
        with pytest.raises(ErroIA, match="500"):
            pergunta._ollama("/api/chat", {})
