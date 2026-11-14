"""
Serviço da tela de pergunta: busca os trechos mais relevantes no pgvector e
pede ao LLM do Ollama uma resposta em português citando as fontes.

É a mesma lógica do `responder.py` (mesmo prompt, mesmo formato de contexto),
mas sem `sys.exit`/`print`: devolve um dict pronto para a API e levanta
`ErroIA` quando o Ollama ou o banco não respondem.

Os trechos vêm de `busca_vetorial.buscar_por_vetor` (AKA-51): só documentos
disponíveis que o usuário que pergunta tem permissão de acessar (S2-26), mais
a base pública do EverySpec. O filtro é feito na query, antes da IA: um trecho
que o usuário não pode ver nunca chega ao prompt, nem como contexto.
disponíveis, mais a base pública do EverySpec.

Resposta só com base nos trechos (AKA-22):
    - sem trecho acima da similaridade mínima, a IA nem é chamada;
    - o prompt proíbe completar lacunas e manda devolver só a frase de "não
      encontrei" quando os trechos não têm a resposta;
    - resposta que começa com essa frase, ou que não cita nenhum trecho
      enviado ([1], [2]...), vira o aviso de "não encontrei", sem fontes;
    - a geração roda só no Ollama local (mesma checagem da D1 do bge-m3).

Origem de cada resposta (AKA-24), em campos separados:
    - `trechos_extraidos`: o texto de cada trecho enviado à IA, literal, como
      está no banco (sem passar pela IA), com documento, revisão, página e
      seção, e se a IA citou o trecho;
    - `interpretacao_ia`: o texto que a IA escreveu (o mesmo de `resposta`,
      mantido para o front atual);
    - `afirmacoes`: a interpretação dividida em frases, cada uma com os
      números dos trechos que a sustentam;
    - `modelos`: embedding e LLM locais que geraram a resposta.
O registro da pergunta guarda tudo isso (`services/rag/registro.py`).
"""

import os
import re
import time

import requests
from django.db import DatabaseError

from core_api.management.commands.testar_bge_m3_local import validar_ollama_local
from core_api.services.rag.busca_vetorial import buscar_por_vetor
from core_api.services.rag.responder import (
    FRASES_NAO_ENCONTROU,
    OPCOES_LLM,
    montar_prompt_sistema,
)
from core_api.services.rag.secoes import secao_no_texto

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODELO_LLM = os.getenv("RAG_MODELO_LLM", "llama3.2")
MODELO_EMBEDDING = os.getenv("RAG_MODELO_EMBEDDING", "bge-m3")
TOP_K = int(os.getenv("RAG_TOP_K", "5"))
# Abaixo disso o trecho é considerado sem relação com a pergunta. Medido com o
# bge-m3 no acervo atual: perguntas do domínio ficam em ~0.55+, perguntas fora
# do acervo (receita, futebol, câmbio) ficam em 0.33-0.46.
SIMILARIDADE_MINIMA = float(os.getenv("RAG_SIMILARIDADE_MINIMA", "0.5"))

MENSAGENS_NAO_ENCONTROU = FRASES_NAO_ENCONTROU
MENSAGEM_NAO_ENCONTROU = FRASES_NAO_ENCONTROU["pt-BR"]

# Parênteses logo após o código que indicam revisão: (REV. B), (BASELINE),
# (REV. B, CHANGE 003), (VER 1.0), (V1.0)
RE_REVISAO_PARENTESES = re.compile(r"\b(REV|BASELINE|CHANGE|VER|V\d)", re.IGNORECASE)
# Revisão como letra no fim do código: FED-STD-123H, MIL-HDBK-17/1F
RE_REVISAO_SUFIXO = re.compile(r"\d([A-Z]{1,2})$")
# Citação de trecho: [1], [2] ou várias juntas, [1, 3]
RE_CITACAO = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# Começos de resposta em que a própria IA diz que os trechos não têm a
# resposta (a frase do prompt e as variações que o modelo costuma usar).
INICIOS_NAO_ENCONTROU = (
    "não encontrei essa informação",
    "nao encontrei essa informacao",
    "i did not find this information",
    "i could not find this information",
    "i couldn't find this information",
)
# Item de lista no começo da linha: "- ", "* ", "• ", "1. ", "1) "
RE_ITEM_DE_LISTA = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
# Fim de frase: pontuação seguida de espaço e de começo de outra frase. Não
# quebra "p. 7", "4.1", "ex.: CA0532" nem "prazo. [1]".
RE_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý\"“(])")


class ErroIA(Exception):
    """O Ollama ou o banco vetorial não responderam como esperado."""


def _ollama(caminho, dados):
    # D1: embedding e geração rodam só no Ollama da própria máquina.
    try:
        validar_ollama_local(OLLAMA_URL)
    except ValueError as e:
        raise ErroIA(str(e)) from e
    try:
        r = requests.post(f"{OLLAMA_URL}{caminho}", json=dados, timeout=600)
    except requests.RequestException as e:
        raise ErroIA(f"Não consegui conectar ao Ollama em {OLLAMA_URL}.") from e
    if r.status_code != 200:
        raise ErroIA(f"Erro do Ollama ({r.status_code}): {r.text[:300]}")
    return r.json()


def buscar_trechos(pergunta, usuario_id, k=TOP_K):
    """Devolve os k trechos mais parecidos com a pergunta, do mais parecido ao
    menos, só entre o que `usuario_id` pode acessar."""
    vetor = _ollama("/api/embed", {"model": MODELO_EMBEDDING, "input": [pergunta]})[
        "embeddings"
    ][0]
    try:
        return buscar_por_vetor(vetor, k, MODELO_EMBEDDING, usuario_id)
    except DatabaseError as e:
        raise ErroIA("Não consegui consultar a base de documentos.") from e


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
    if p1 is None:
        return "—"
    return str(p1) if p1 == p2 or p2 is None else f"{p1}-{p2}"


def _montar_fonte(numero, trecho, citadas):
    documento_id = trecho.get("documento_id")
    if documento_id:
        # Documento do sistema: código e revisão vêm dos metadados do upload.
        codigo, revisao = trecho["documento"], trecho.get("revisao")
    else:
        # Base pública: código e revisão saem do título do EverySpec.
        codigo, revisao = extrair_codigo_e_revisao(
            trecho["titulo"], trecho["documento"]
        )
    return {
        "numero": numero,
        # Abre o documento no visualizador seguro do front (AKA-61);
        # None na base pública, que não tem Documento.
        "documento_id": documento_id,
        "trecho_id": trecho.get("trecho_id"),
        "documento": codigo,
        "revisao": revisao,
        "pagina": formatar_paginas(trecho["pagina_inicio"], trecho["pagina_fim"]),
        "pagina_inicio": trecho["pagina_inicio"],
        "pagina_fim": trecho["pagina_fim"],
        # Base pública não guarda a seção: lê do próprio texto do trecho.
        "secao": trecho.get("secao") or secao_no_texto(trecho["texto"]),
        "titulo": trecho["titulo"],
        "url_pdf": trecho["url_pdf"] or None,
        "similaridade": round(trecho["similaridade"], 3),
        "citada": numero in citadas,
    }


def perguntar_ia(pergunta, trechos, idioma="pt-BR"):
    contexto = "\n\n".join(
        f"[{n}] Documento: {t['titulo'] or t['documento']} | "
        f"p. {formatar_paginas(t['pagina_inicio'], t['pagina_fim'])}\n"
        f"{' '.join(t['texto'].split())}"
        for n, t in enumerate(trechos, 1)
    )
    if idioma == "en":
        conteudo_usuario = (
            f"Document excerpts:\n\n{contexto}\n\n"
            f"Question: {pergunta}\n"
            "Answer in English:"
        )
    else:
        conteudo_usuario = (
            f"Trechos dos documentos:\n\n{contexto}\n\nPergunta: {pergunta}"
        )

    dados = _ollama(
        "/api/chat",
        {
            "model": MODELO_LLM,
            "messages": [
                {"role": "system", "content": montar_prompt_sistema(idioma)},
                {
                    "role": "user",
                    "content": conteudo_usuario,
                },
            ],
            "stream": False,
            "options": OPCOES_LLM,
        },
    )
    return dados.get("message", {}).get("content", "").strip()


def citacoes_validas(resposta, quantidade_trechos):
    """Números de trecho citados na resposta que existem de fato (1 a N)."""
    return {
        int(numero)
        for grupo in RE_CITACAO.findall(resposta)
        for numero in grupo.split(",")
        if 1 <= int(numero) <= quantidade_trechos
    }


def dividir_afirmacoes(resposta, quantidade_trechos):
    """A interpretação da IA em afirmações, cada uma com os trechos citados.

    Divide por linha e, dentro da linha, por frase. Item de lista sem citação
    herda as fontes da linha que abre a lista ("As ferramentas são [1]:").
    Afirmação sem nenhuma fonte fica com `fontes: []`: não é rastreável.
    """
    afirmacoes = []
    fontes_da_lista = []
    for linha in resposta.splitlines():
        if not linha.strip():
            continue
        item = RE_ITEM_DE_LISTA.match(linha)
        frases = (
            [RE_ITEM_DE_LISTA.sub("", linha)]
            if item
            else RE_FIM_DE_FRASE.split(linha.strip())
        )
        for frase in frases:
            frase = frase.strip()
            if not frase:
                continue
            fontes = sorted(citacoes_validas(frase, quantidade_trechos))
            if not fontes and item:
                fontes = list(fontes_da_lista)
            afirmacoes.append({"texto": frase, "fontes": fontes})
        if not item:
            abre_lista = linha.rstrip().endswith(":") and afirmacoes
            fontes_da_lista = afirmacoes[-1]["fontes"] if abre_lista else []
    return afirmacoes


def _trecho_extraido(fonte, trecho):
    """O trecho do documento, literal, separado da interpretação da IA."""
    return {
        "numero": fonte["numero"],
        "citado": fonte["citada"],
        "documento_id": fonte["documento_id"],
        "documento": fonte["documento"],
        "revisao": fonte["revisao"],
        "pagina": fonte["pagina"],
        "secao": fonte["secao"],
        # Exatamente o texto guardado no banco, sem reescrita.
        "texto": trecho["texto"],
    }


def ia_nao_encontrou(resposta):
    """True se a resposta da IA começa dizendo que não achou nos trechos."""
    inicio = " ".join(resposta.lower().split()).lstrip("\"'“*>#- ")
    return inicio.startswith(INICIOS_NAO_ENCONTROU)


def responder_pergunta(pergunta, idioma="pt-BR", *, usuario_id):
    """
    Fluxo completo da tela de pergunta, para o usuário `usuario_id` (uuid do
    JWT; obrigatório: define quais documentos a IA pode usar). Devolve:

        {
            "resposta": str,
            "encontrou": bool,
            "idioma": str,
            "fontes": [{numero, documento_id, trecho_id, documento, revisao,
                        pagina, secao, citada, ...}],
            "trechos_extraidos": [{numero, citado, documento, revisao, pagina,
                                   secao, texto}],  # texto literal
            "interpretacao_ia": str,              # igual a "resposta"
            "afirmacoes": [{"texto": str, "fontes": [int]}],
            "modelos": {"embedding": str, "llm": str | None},
            "tempos_ms": {"busca": int, "ia": int, "total": int},
        }

    Sem base nos documentos: `encontrou` False e as listas vazias.
    """
    if not idioma:
        idioma = "pt-BR"

    inicio = time.perf_counter()
    trechos = [
        t
        for t in buscar_trechos(pergunta, usuario_id)
        if t["similaridade"] >= SIMILARIDADE_MINIMA
    ]
    fim_busca = time.perf_counter()

    mensagem_nao_encontrou = MENSAGENS_NAO_ENCONTROU.get(idioma, MENSAGEM_NAO_ENCONTROU)
    resposta = mensagem_nao_encontrou
    citadas = set()
    modelo_llm = None
    if trechos:
        resposta = perguntar_ia(pergunta, trechos, idioma=idioma)
        modelo_llm = MODELO_LLM
        citadas = citacoes_validas(resposta, len(trechos))
        # A IA disse que não achou, ou respondeu sem citar nenhum trecho
        # enviado (resposta sem base nos documentos): devolve o aviso padrão.
        if ia_nao_encontrou(resposta) or not citadas:
            resposta = mensagem_nao_encontrou
            citadas = set()
    fim_ia = time.perf_counter()

    encontrou = bool(citadas)
    usados = trechos if encontrou else []
    fontes = [_montar_fonte(n, t, citadas) for n, t in enumerate(usados, 1)]

    return {
        "resposta": resposta,
        "encontrou": encontrou,
        "idioma": idioma,
        "fontes": fontes,
        "trechos_extraidos": [
            _trecho_extraido(fonte, trecho) for fonte, trecho in zip(fontes, usados)
        ],
        "interpretacao_ia": resposta,
        "afirmacoes": (dividir_afirmacoes(resposta, len(usados)) if encontrou else []),
        "modelos": {"embedding": MODELO_EMBEDDING, "llm": modelo_llm},
        "tempos_ms": {
            "busca": round((fim_busca - inicio) * 1000),
            "ia": round((fim_ia - fim_busca) * 1000),
            "total": round((fim_ia - inicio) * 1000),
        },
    }
