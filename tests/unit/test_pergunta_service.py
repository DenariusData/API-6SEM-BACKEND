import uuid
from unittest.mock import Mock, patch

import pytest
import requests

from core_api.services.rag import pergunta
from core_api.services.rag.pergunta import (
    ErroIA,
    formatar_paginas,
    responder_pergunta,
)
from core_api.services.rag.responder import (
    FRASES_NAO_ENCONTROU,
    montar_prompt_sistema,
)


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


USUARIO = uuid.uuid4()


def responder(trechos, resposta_ia="Resposta [1]", idioma="pt-BR"):
    with (
        patch.object(pergunta, "buscar_trechos", return_value=trechos),
        patch.object(pergunta, "perguntar_ia", return_value=resposta_ia) as ia,
    ):
        return (
            responder_pergunta(
                "Como marcar a carga?", idioma=idioma, usuario_id=USUARIO
            ),
            ia,
        )


def test_a_busca_recebe_o_usuario_que_perguntou():
    with (
        patch.object(pergunta, "buscar_trechos", return_value=[]) as busca,
        patch.object(pergunta, "perguntar_ia"),
    ):
        responder_pergunta("Como marcar a carga?", usuario_id=USUARIO)

    assert busca.call_args.args == ("Como marcar a carga?", USUARIO)


def test_usuario_e_obrigatorio_para_responder():
    with pytest.raises(TypeError):
        responder_pergunta("Como marcar a carga?")


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


# -- AKA-22: resposta só com base nos trechos ------------------------------
@pytest.mark.parametrize("idioma", ["pt-BR", "en"])
def test_resposta_sem_citacao_vira_nao_encontrei(idioma):
    resultado, _ = responder(
        [trecho(0.9)], resposta_ia="A marcação usa tinta preta.", idioma=idioma
    )

    assert resultado["encontrou"] is False
    assert resultado["resposta"] == FRASES_NAO_ENCONTROU[idioma]
    assert resultado["fontes"] == []


def test_citacao_de_trecho_que_nao_foi_enviado_nao_conta():
    resultado, _ = responder([trecho(0.9)], resposta_ia="Conforme o trecho [7].")

    assert resultado["encontrou"] is False
    assert resultado["resposta"] == FRASES_NAO_ENCONTROU["pt-BR"]


def test_nao_encontrei_seguido_de_suposicao_devolve_so_o_aviso():
    resposta_ia = (
        "Não encontrei essa informação nos documentos consultados. "
        "Mas provavelmente o prazo é de 10 dias [1]."
    )
    resultado, _ = responder([trecho(0.9)], resposta_ia=resposta_ia)

    assert resultado["encontrou"] is False
    assert resultado["resposta"] == FRASES_NAO_ENCONTROU["pt-BR"]
    assert resultado["fontes"] == []


@pytest.mark.parametrize(
    "resposta_ia",
    [
        "Não encontrei essa informação nos trechos fornecidos.",
        '"Não encontrei essa informação nos documentos consultados."',
        "I could not find this information in the excerpts.",
    ],
)
def test_variacoes_da_frase_de_nao_encontrei(resposta_ia):
    resultado, _ = responder([trecho(0.9)], resposta_ia=resposta_ia)

    assert resultado["encontrou"] is False
    assert resultado["fontes"] == []


def test_resposta_parcial_com_citacao_continua_encontrada():
    resposta_ia = (
        "A carga deve ser marcada na lateral [1]. "
        "Os trechos não informam a cor da tinta."
    )
    resultado, _ = responder([trecho(0.9)], resposta_ia=resposta_ia)

    assert resultado["encontrou"] is True
    assert resultado["resposta"] == resposta_ia
    assert resultado["fontes"][0]["citada"] is True


def test_citacao_de_varios_trechos_juntos():
    resultado, _ = responder(
        [trecho(0.9), trecho(0.8), trecho(0.7)], resposta_ia="Marcação lateral [1, 3]."
    )

    citadas = [f["citada"] for f in resultado["fontes"]]
    assert citadas == [True, False, True]


def test_ollama_fora_da_maquina_local_e_recusado():
    with (
        patch.object(pergunta, "OLLAMA_URL", "http://10.0.0.5:11434"),
        patch.object(pergunta.requests, "post") as post,
    ):
        with pytest.raises(ErroIA, match="não é a máquina local"):
            pergunta._ollama("/api/chat", {})

    post.assert_not_called()


@pytest.mark.parametrize("idioma", ["pt-BR", "en"])
def test_perguntar_ia_usa_prompt_dos_trechos_e_temperatura_zero(idioma):
    with patch.object(
        pergunta, "_ollama", return_value={"message": {"content": "Ok [1]"}}
    ) as ollama:
        resposta = pergunta.perguntar_ia("Como marcar?", [trecho(0.9)], idioma)

    assert resposta == "Ok [1]"
    caminho, dados = ollama.call_args.args
    assert caminho == "/api/chat"
    assert dados["model"] == pergunta.MODELO_LLM
    assert dados["options"]["temperature"] == 0
    sistema, usuario = dados["messages"]
    assert sistema["content"] == montar_prompt_sistema(idioma)
    assert "[1] Documento: FED-STD-123H" in usuario["content"]
    assert "Marking shall be ..." in usuario["content"]


# -- AKA-24: origem da resposta --------------------------------------------
TEXTO_LITERAL = "4.1 General\nMarking shall be  applied on   two sides."


def trecho_do_sistema(similaridade=0.9, texto=TEXTO_LITERAL, secao="4.1 General"):
    return {
        "documento_id": "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90",
        "trecho_id": "42",
        "documento": "PI-TC-001",
        "revisao": "A",
        "titulo": "Procedimento de inspeção",
        "url_pdf": None,
        "pagina_inicio": 2,
        "pagina_fim": 2,
        "secao": secao,
        "texto": texto,
        "similaridade": similaridade,
    }


def test_trecho_extraido_vem_literal_e_separado_da_interpretacao():
    resultado, _ = responder(
        [trecho_do_sistema()], resposta_ia="A marcação vai nos dois lados [1]."
    )

    assert resultado["interpretacao_ia"] == "A marcação vai nos dois lados [1]."
    assert resultado["resposta"] == resultado["interpretacao_ia"]
    [extraido] = resultado["trechos_extraidos"]
    # Sem reescrita: quebras de linha e espaços duplos como estão no banco.
    assert extraido["texto"] == TEXTO_LITERAL
    assert extraido == {
        "numero": 1,
        "citado": True,
        "documento_id": "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90",
        "documento": "PI-TC-001",
        "revisao": "A",
        "pagina": "2",
        "secao": "4.1 General",
        "texto": TEXTO_LITERAL,
    }


def test_fonte_traz_documento_revisao_pagina_e_secao():
    resultado, _ = responder([trecho_do_sistema()], resposta_ia="Resposta [1].")

    fonte = resultado["fontes"][0]
    assert (fonte["documento"], fonte["revisao"], fonte["pagina"]) == (
        "PI-TC-001",
        "A",
        "2",
    )
    assert fonte["secao"] == "4.1 General"
    assert fonte["trecho_id"] == "42"


def test_base_publica_le_a_secao_do_texto_do_trecho():
    publico = trecho(0.9)
    publico["texto"] = "Marking shall be ...\n5.3 Container markings\nThe ..."
    resultado, _ = responder([publico], resposta_ia="Resposta [1].")

    assert resultado["fontes"][0]["secao"] == "5.3 Container markings"


def test_cada_afirmacao_aponta_os_trechos_de_origem():
    resposta_ia = (
        "A marcação vai nos dois lados [1]. O prazo é de 10 dias [2].\n"
        "As ferramentas necessárias são [1, 2]:\n"
        "- Lanterna de alta intensidade\n"
        "- Lupa de dez aumentos [2]\n"
        "Os trechos não informam a cor da tinta."
    )
    resultado, _ = responder(
        [trecho_do_sistema(), trecho_do_sistema(0.8)], resposta_ia=resposta_ia
    )

    assert resultado["afirmacoes"] == [
        {"texto": "A marcação vai nos dois lados [1].", "fontes": [1]},
        {"texto": "O prazo é de 10 dias [2].", "fontes": [2]},
        {"texto": "As ferramentas necessárias são [1, 2]:", "fontes": [1, 2]},
        # item de lista sem citação herda a da linha que abre a lista
        {"texto": "Lanterna de alta intensidade", "fontes": [1, 2]},
        {"texto": "Lupa de dez aumentos [2]", "fontes": [2]},
        # sem citação: fica sem fonte, ou seja, não rastreável
        {"texto": "Os trechos não informam a cor da tinta.", "fontes": []},
    ]


def test_afirmacao_nao_quebra_em_abreviacao_nem_numero():
    resultado, _ = responder(
        [trecho_do_sistema()],
        resposta_ia="Conforme a seção 4.1, ver p. 7 e ex.: CA0532-PO [1].",
    )

    assert len(resultado["afirmacoes"]) == 1
    assert resultado["afirmacoes"][0]["fontes"] == [1]


def test_trechos_nao_citados_tambem_vao_com_o_texto_literal():
    resultado, _ = responder(
        [trecho_do_sistema(), trecho_do_sistema(0.8, texto="Outro trecho")],
        resposta_ia="Resposta [1].",
    )

    citados = [(t["numero"], t["citado"]) for t in resultado["trechos_extraidos"]]
    assert citados == [(1, True), (2, False)]
    assert resultado["trechos_extraidos"][1]["texto"] == "Outro trecho"


def test_modelos_locais_que_geraram_a_resposta():
    resultado, _ = responder([trecho_do_sistema()], resposta_ia="Resposta [1].")

    assert resultado["modelos"] == {
        "embedding": pergunta.MODELO_EMBEDDING,
        "llm": pergunta.MODELO_LLM,
    }


@pytest.mark.parametrize("resposta_ia", ["Sem citação.", FRASES_NAO_ENCONTROU["pt-BR"]])
def test_sem_base_devolve_as_listas_de_origem_vazias(resposta_ia):
    resultado, _ = responder([trecho_do_sistema()], resposta_ia=resposta_ia)

    assert resultado["trechos_extraidos"] == []
    assert resultado["afirmacoes"] == []
    assert resultado["interpretacao_ia"] == FRASES_NAO_ENCONTROU["pt-BR"]


def test_sem_trecho_relevante_a_ia_nao_gera_nada():
    resultado, ia = responder([trecho_do_sistema(0.1)])

    ia.assert_not_called()
    assert resultado["modelos"]["llm"] is None
