"""
Serviço da tela de pergunta: busca os trechos mais relevantes no pgvector e
pede ao LLM do Ollama uma resposta em português citando as fontes.

É a mesma lógica do `responder.py` (mesmo prompt, mesmo formato de contexto),
mas sem `sys.exit`/`print`: devolve um dict pronto para a API e levanta
`ErroIA` quando o Ollama ou o banco não respondem.
"""

import os
import re
import time

import psycopg
import requests

from core_api.services.rag.responder import PROMPT_SISTEMA

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODELO_LLM = os.getenv("RAG_MODELO_LLM", "llama3.2")
MODELO_EMBEDDING = os.getenv("RAG_MODELO_EMBEDDING", "bge-m3")
TOP_K = int(os.getenv("RAG_TOP_K", "5"))
# Abaixo disso o trecho é considerado sem relação com a pergunta. Medido com o
# bge-m3 no acervo atual: perguntas do domínio ficam em ~0.55+, perguntas fora
# do acervo (receita, futebol, câmbio) ficam em 0.33-0.46.
SIMILARIDADE_MINIMA = float(os.getenv("RAG_SIMILARIDADE_MINIMA", "0.5"))

MENSAGEM_NAO_ENCONTROU = "Não encontrei essa informação nos documentos consultados."

# Parênteses logo após o código que indicam revisão: (REV. B), (BASELINE),
# (REV. B, CHANGE 003), (VER 1.0), (V1.0)
RE_REVISAO_PARENTESES = re.compile(r"\b(REV|BASELINE|CHANGE|VER|V\d)", re.IGNORECASE)
# Revisão como letra no fim do código: FED-STD-123H, MIL-HDBK-17/1F
RE_REVISAO_SUFIXO = re.compile(r"\d([A-Z]{1,2})$")
RE_CITACAO = re.compile(r"\[(\d+)\]")


class ErroIA(Exception):
    """O Ollama ou o banco vetorial não responderam como esperado."""


def _ollama(caminho, dados):
    try:
        r = requests.post(f"{OLLAMA_URL}{caminho}", json=dados, timeout=600)
    except requests.RequestException as e:
        raise ErroIA(f"Não consegui conectar ao Ollama em {OLLAMA_URL}.") from e
    if r.status_code != 200:
        raise ErroIA(f"Erro do Ollama ({r.status_code}): {r.text[:300]}")
    return r.json()


def buscar_trechos(pergunta, k=TOP_K):
    """Devolve os k trechos mais parecidos com a pergunta, do mais parecido ao menos."""
    vetor = _ollama("/api/embed", {"model": MODELO_EMBEDDING, "input": [pergunta]})[
        "embeddings"
    ][0]
    vetor = "[" + ",".join(f"{x:.7g}" for x in vetor) + "]"
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            linhas = conn.execute(
                """
                SELECT documento, titulo, url_pdf, pagina_inicio, pagina_fim, texto,
                       1 - (embedding <=> %(vetor)s::vector) AS similaridade
                FROM rag_chunks
                ORDER BY embedding <=> %(vetor)s::vector
                LIMIT %(k)s
                """,
                {"vetor": vetor, "k": k},
            ).fetchall()
    except psycopg.Error as e:
        raise ErroIA("Não consegui consultar a base de documentos.") from e

    colunas = ("documento", "titulo", "url_pdf", "pagina_inicio", "pagina_fim")
    return [
        dict(zip(colunas, linha[:5]), texto=linha[5], similaridade=float(linha[6]))
        for linha in linhas
    ]


def extrair_codigo_e_revisao(titulo, documento):
    """
    Separa o código do documento e a revisão a partir do título do EverySpec.

        "CxP 70000 (REV. B), CONSTELLATION ..."  -> ("CxP 70000", "REV. B")
        "FED-STD-123H, FEDERAL STANDARD: ..."    -> ("FED-STD-123H", "H")
        "MIL-HDBK-21, MILITARY HANDBOOK: ..."    -> ("MIL-HDBK-21", None)
    """
    if not titulo:
        # "NASA/CxP_70057_RevB.031900.pdf" -> "CxP_70057_RevB"
        return documento.rsplit("/", 1)[-1].split(".")[0], None

    m = re.match(r"\s*([^,(]+)(?:\(([^)]*)\))?", titulo)
    codigo = m.group(1).strip()
    parenteses = (m.group(2) or "").strip(" _")
    if parenteses and RE_REVISAO_PARENTESES.search(parenteses):
        return codigo, parenteses
    sufixo = RE_REVISAO_SUFIXO.search(codigo)
    return codigo, sufixo.group(1) if sufixo else None


def formatar_paginas(p1, p2):
    return str(p1) if p1 == p2 else f"{p1}-{p2}"


def _montar_fonte(numero, trecho, citadas):
    codigo, revisao = extrair_codigo_e_revisao(trecho["titulo"], trecho["documento"])
    return {
        "numero": numero,
        "documento": codigo,
        "revisao": revisao,
        "pagina": formatar_paginas(trecho["pagina_inicio"], trecho["pagina_fim"]),
        "pagina_inicio": trecho["pagina_inicio"],
        "pagina_fim": trecho["pagina_fim"],
        "titulo": trecho["titulo"],
        "url_pdf": trecho["url_pdf"] or None,
        "similaridade": round(trecho["similaridade"], 3),
        "citada": numero in citadas,
    }


def perguntar_ia(pergunta, trechos):
    contexto = "\n\n".join(
        f"[{n}] Documento: {t['titulo'] or t['documento']} | "
        f"p. {formatar_paginas(t['pagina_inicio'], t['pagina_fim'])}\n"
        f"{' '.join(t['texto'].split())}"
        for n, t in enumerate(trechos, 1)
    )
    dados = _ollama(
        "/api/chat",
        {
            "model": MODELO_LLM,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEMA},
                {
                    "role": "user",
                    "content": f"Trechos dos documentos:\n\n{contexto}\n\n"
                    f"Pergunta: {pergunta}",
                },
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        },
    )
    return dados.get("message", {}).get("content", "").strip()


def responder_pergunta(pergunta):
    """
    Fluxo completo da tela de pergunta. Devolve:

        {
            "resposta": str,
            "encontrou": bool,
            "fontes": [{numero, documento, revisao, pagina, ...}],
            "tempos_ms": {"busca": int, "ia": int, "total": int},
        }
    """
    inicio = time.perf_counter()
    trechos = [
        t for t in buscar_trechos(pergunta) if t["similaridade"] >= SIMILARIDADE_MINIMA
    ]
    fim_busca = time.perf_counter()

    resposta = MENSAGEM_NAO_ENCONTROU
    if trechos:
        resposta = perguntar_ia(pergunta, trechos)
    fim_ia = time.perf_counter()

    # Sem trecho relevante nem chamamos a IA; se chamamos e ela mesma disse que
    # não achou nos trechos, também tratamos como "não encontrou"
    encontrou = (
        bool(trechos) and "não encontrei essa informação" not in resposta.lower()
    )
    citadas = {int(n) for n in RE_CITACAO.findall(resposta)}
    fontes = (
        [_montar_fonte(n, t, citadas) for n, t in enumerate(trechos, 1)]
        if encontrou
        else []
    )

    return {
        "resposta": resposta,
        "encontrou": encontrou,
        "fontes": fontes,
        "tempos_ms": {
            "busca": round((fim_busca - inicio) * 1000),
            "ia": round((fim_ia - fim_busca) * 1000),
            "total": round((fim_ia - inicio) * 1000),
        },
    }
