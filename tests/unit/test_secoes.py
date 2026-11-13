import pytest

from core_api.services.rag.secoes import (
    secao_no_texto,
    secoes_dos_trechos,
    titulo_de_secao,
)


@pytest.mark.parametrize(
    "linha, esperado",
    [
        ("5. Critérios de inspeção visual", "5. Critérios de inspeção visual"),
        ("4.1.2 Marking of containers", "4.1.2 Marking of containers"),
        ("4.1 General. The marking shall be applied", "4.1 General"),
        ("1 Scope", "1 Scope"),
        ("4.3 Type 2", "4.3 Type 2"),
        ("SECTION 3 - REQUIREMENTS", "SECTION 3 - REQUIREMENTS"),
        ("SECTION 3", "SECTION 3"),
        ("APPENDIX A.", "APPENDIX A"),
        ("  7.   Liberação   da aeronave  ", "7. Liberação da aeronave"),
    ],
)
def test_reconhece_titulo_de_secao(linha, esperado):
    assert titulo_de_secao(linha) == esperado


@pytest.mark.parametrize(
    "linha",
    [
        "1. Confira se o Python está instalado.",  # item de lista com frase
        "2. Instale o Locust:",
        "3.2 Marking of containers ........ 12",  # sumário
        "3.2 Marking of containers      12",
        "10 mm",  # medida
        "20 peças por lote",  # linha quebrada do PDF
        "4.5 mm. A peça segue",
        "2026 11 05",
        "PROCEDIMENTO INTERNO DE TESTE PI-TC-001",  # cabeçalho sem número
        "Este é um texto normal.",
        "",
    ],
)
def test_nao_confunde_texto_com_titulo(linha):
    assert titulo_de_secao(linha) is None


def test_secao_de_cada_trecho_e_a_que_vale_na_primeira_linha():
    trechos = [
        "Introdução sem título\n1. Objetivo\nTexto a\nTexto b",
        # começa repetindo "Texto a/Texto b" (sobreposição): ainda é a seção 1
        "Texto a\nTexto b\nmais texto\n2. Aplicação\nfim",
        "2. Aplicação\nfim\nsegue",
        # a sobreposição "fim/segue" vem depois do título 2
        "fim\nsegue\noutra linha",
    ]

    assert secoes_dos_trechos(trechos) == [
        "1. Objetivo",  # antes de qualquer título: o primeiro dentro do trecho
        "1. Objetivo",
        "2. Aplicação",
        "2. Aplicação",
    ]


def test_sobreposicao_que_comeca_no_titulo_ja_esta_na_secao_nova():
    trechos = [
        "1. Escopo\nlinha 1\nlinha 2\n2. Requisitos\nlinha 3",
        # repete "2. Requisitos/linha 3": começa no próprio título 2
        "2. Requisitos\nlinha 3\nlinha 4",
        # repete só "linha 4": começa depois do título 2
        "linha 4\nlinha 5",
    ]

    assert secoes_dos_trechos(trechos) == [
        "1. Escopo",
        "2. Requisitos",
        "2. Requisitos",
    ]


def test_documento_sem_titulos_fica_sem_secao():
    assert secoes_dos_trechos(["texto corrido", "mais texto"]) == [None, None]


def test_secao_no_texto_avulso():
    assert secao_no_texto("Marking shall be ...\n4.1 General\nmais") == "4.1 General"
    assert secao_no_texto("Marking shall be ...") is None


def test_trecho_que_comeca_antes_do_titulo_fica_na_secao_anterior():
    trechos = [
        "1. Escopo\nlinha 1\nlinha 2\n2. Requisitos",
        # repete "linha 2/2. Requisitos": começa em "linha 2", ainda na seção 1
        "linha 2\n2. Requisitos\nlinha 3",
    ]

    assert secoes_dos_trechos(trechos) == ["1. Escopo", "1. Escopo"]
