"""
Seção do documento de onde veio cada trecho (AKA-24).

O PDF não informa a seção; ela é lida dos títulos numerados do próprio texto,
como as normas costumam escrever:

    "5. Critérios de inspeção visual"      -> "5. Critérios de inspeção visual"
    "4.1.2 Marking of containers"           -> "4.1.2 Marking of containers"
    "4.1 General. The marking shall be..."  -> "4.1 General"
    "SECTION 3 - REQUIREMENTS"              -> "SECTION 3 - REQUIREMENTS"
    "APPENDIX A"                            -> "APPENDIX A"

Frase de lista ("1. Confira se o Python está instalado."), linha de sumário
("3.2 Marking ........ 12") e números soltos não contam como título.

A seção de um trecho é a que está valendo na primeira linha dele: o título
mais recente antes dessa linha, olhando os trechos anteriores do mesmo
documento. Como trechos vizinhos se sobrepõem, o título que aparece só na
sobreposição não conta para o início do trecho seguinte.
"""

import re

TAMANHO_MAXIMO_TITULO = 100

# "5. Título", "4.1.2 Título", "SECTION 3 - TÍTULO", "APPENDIX A", "ANEXO B"
RE_TITULO = re.compile(
    r"""^(?P<titulo>
        (?:
            (?:SECTION|SEÇÃO|SECAO|CHAPTER|CAPÍTULO|CAPITULO|APPENDIX|ANNEX
              |ANEXO|APÊNDICE|APENDICE)\s+[0-9A-Z]{1,3}(?:\.\d{1,3})*\.?
            (?:\s*[-–—:]?\s+\S.*)?
          |
            \d{1,2}(?:\.\d{1,3}){0,4}\.?\s+\S.*
        )
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
# Título na mesma linha do texto, comum em normas militares:
# "4.1 General. The marking shall be..." -> "4.1 General"
RE_TITULO_EM_LINHA = re.compile(
    r"^(?P<titulo>\d{1,2}(?:\.\d{1,3}){1,4}\.?\s+[^\W\d][^.]{0,60})\.\s+\S"
)
# Linha de sumário: pontilhado, ou número de página separado por vários
# espaços no fim ("3.2 Marking of containers      12").
RE_SUMARIO = re.compile(r"\.{4,}|\s{2,}\d{1,4}\s*$")


def titulo_de_secao(linha):
    """O título de seção que a linha começa, ou None se ela não é título."""
    if RE_SUMARIO.search(linha):
        return None
    linha = " ".join(linha.split())
    if not linha:
        return None

    em_linha = RE_TITULO_EM_LINHA.match(linha)
    if em_linha:
        linha = em_linha.group("titulo")
    elif len(linha) > TAMANHO_MAXIMO_TITULO or not RE_TITULO.match(linha):
        return None
    # Frase de lista ou de texto, não título: termina com pontuação de frase.
    elif linha.endswith((".", ";", ",", ":", "?", "!")) and not _so_rotulo(linha):
        return None
    texto = re.sub(r"^[\d.\s]+", "", linha)
    # Depois do número, o título começa com maiúscula e tem palavra de verdade:
    # descarta medida ("10 mm") e linha quebrada do PDF ("20 peças por lote").
    if linha[0].isdigit() and not texto[:1].isupper():
        return None
    if not re.search(r"[^\W\d_]{3,}", texto) and not _so_rotulo(linha + "."):
        return None
    return linha.rstrip(". ")


def _so_rotulo(linha):
    """ "APPENDIX A." ou "SECTION 3." sem título: o ponto final não é de frase."""
    return bool(re.fullmatch(r"[^\W\d_]+\s+[0-9A-Z]{1,3}(?:\.\d{1,3})*\.", linha))


def _linhas(texto):
    return [linha.strip() for linha in texto.split("\n") if linha.strip()]


def _sobreposicao(anteriores, atuais):
    """Quantas linhas do início do trecho atual repetem o fim do anterior."""
    for k in range(min(len(anteriores) - 1, len(atuais)), 0, -1):
        if anteriores[-k:] == atuais[:k]:
            return k
    return 0


def secao_no_texto(texto):
    """Primeiro título de seção dentro do texto (trecho avulso, sem os
    vizinhos, como os da base pública)."""
    for linha in _linhas(texto):
        titulo = titulo_de_secao(linha)
        if titulo:
            return titulo
    return None


def secoes_dos_trechos(textos):
    """Seção de cada trecho de um documento, na ordem dos trechos.

    Para cada linha guarda o título que vale depois dela; o início do trecho
    seguinte herda o título que valia antes da parte repetida (sobreposição).
    Trecho do começo do documento, antes de qualquer título, fica com o
    primeiro título que aparecer nele, ou None.
    """
    secoes = []
    linhas_anteriores, vigor_anterior = [], []
    for texto in textos:
        linhas = _linhas(texto)
        # Última linha do trecho anterior que não se repete no início deste.
        corte = len(linhas_anteriores) - _sobreposicao(linhas_anteriores, linhas) - 1
        vigor_no_inicio = vigor_anterior[corte] if corte >= 0 else None

        vigor, atual = [], vigor_no_inicio
        for linha in linhas:
            atual = titulo_de_secao(linha) or atual
            vigor.append(atual)

        secao = vigor[0] if vigor else vigor_no_inicio
        secoes.append(secao or secao_no_texto(texto))
        linhas_anteriores, vigor_anterior = linhas, vigor
    return secoes
