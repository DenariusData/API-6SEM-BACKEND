#!/usr/bin/env python3
"""
Responde perguntas com base nos documentos (RAG): busca os trechos mais parecidos no PostgreSQL
e pede ao LLM do Ollama uma resposta em português, citando de onde veio cada informação.

Pré-requisitos:
    ollama pull llama3.2

Uso:
    python responder.py "Como é feito o ensaio de tração em materiais compósitos?"
    python responder.py "Quanto tempo a Orion deve manter a tripulação viva sem pressurização?" --mostrar-trechos
"""

import argparse
import json
import os
import sys
import textwrap

import psycopg
import requests

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

PROMPT_SISTEMA = """Você é um assistente técnico que responde perguntas sobre normas e especificações técnicas.

Regras:
- Responda sempre em português do Brasil, mesmo que os trechos estejam em inglês.
- Use SOMENTE as informações dos trechos fornecidos. Não use conhecimento próprio.
- Se os trechos não tiverem a resposta, diga: "Não encontrei essa informação nos documentos consultados."
- Indique a fonte de cada informação com o número do trecho entre colchetes, por exemplo [1] ou [2].
- Mantenha siglas, códigos e números exatamente como no original (ex.: CA0532-PO, ASTM D3039, 144 horas).
- Seja objetivo."""


def ollama(caminho, dados, **kwargs):
    try:
        r = requests.post(f"{OLLAMA_URL}{caminho}", json=dados, timeout=600, **kwargs)
    except requests.ConnectionError:
        sys.exit(f"\nNão consegui conectar ao Ollama em {OLLAMA_URL}. Confira se ele está aberto.")
    if r.status_code != 200:
        sys.exit(f"\nErro do Ollama ({r.status_code}): {r.text[:300]}\n"
                 f"Se o modelo não foi encontrado, baixe com: ollama pull {dados.get('model')}")
    return r


def buscar_trechos(pergunta, k, modelo_embedding):
    resposta = ollama("/api/embed", {"model": modelo_embedding, "input": [pergunta]})
    vetor = "[" + ",".join(f"{x:.7g}" for x in resposta.json()["embeddings"][0]) + "]"
    with psycopg.connect(DATABASE_URL) as conn:
        return conn.execute(
            """
            SELECT documento, titulo, url_pdf, pagina_inicio, pagina_fim, texto,
                   1 - (embedding <=> %(vetor)s::vector) AS similaridade
            FROM rag_chunks
            ORDER BY embedding <=> %(vetor)s::vector
            LIMIT %(k)s
            """,
            {"vetor": vetor, "k": k},
        ).fetchall()


def formatar_paginas(p1, p2):
    return f"p. {p1}" if p1 == p2 else f"p. {p1}-{p2}"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Responde perguntas com base nos documentos.")
    ap.add_argument("pergunta")
    ap.add_argument("--k", type=int, default=5, help="quantos trechos enviar ao LLM")
    ap.add_argument("--modelo", default="llama3.2", help="LLM do Ollama que escreve a resposta")
    ap.add_argument("--modelo-embedding", default="bge-m3")
    ap.add_argument("--mostrar-trechos", action="store_true", help="mostra os trechos enviados ao LLM")
    args = ap.parse_args()

    trechos = buscar_trechos(args.pergunta, args.k, args.modelo_embedding)
    if not trechos:
        sys.exit("Nenhum chunk no banco. Rode antes o gerar_embeddings.py")

    contexto = "\n\n".join(
        f"[{n}] Documento: {titulo or documento} | {formatar_paginas(p1, p2)}\n{' '.join(texto.split())}"
        for n, (documento, titulo, _, p1, p2, texto, _) in enumerate(trechos, 1)
    )

    if args.mostrar_trechos:
        print("=== Trechos enviados ao LLM ===")
        for n, (documento, _, _, p1, p2, texto, similaridade) in enumerate(trechos, 1):
            print(f"\n[{n}] similaridade {similaridade:.3f} | {documento} ({formatar_paginas(p1, p2)})")
            print(textwrap.indent(textwrap.fill(" ".join(texto.split())[:300], 100), "    "))
        print()

    print(f'Pergunta: "{args.pergunta}"\n')
    print("Resposta: ", end="", flush=True)

    mensagens = [
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": f"Trechos dos documentos:\n\n{contexto}\n\nPergunta: {args.pergunta}"},
    ]
    resposta = ollama("/api/chat", {
        "model": args.modelo,
        "messages": mensagens,
        "stream": True,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }, stream=True)

    # A resposta chega aos poucos, como num chat
    for linha in resposta.iter_lines():
        if not linha:
            continue
        dados = json.loads(linha)
        if "error" in dados:
            sys.exit(f"\nErro do Ollama: {dados['error']}")
        print(dados.get("message", {}).get("content", ""), end="", flush=True)
        if dados.get("done"):
            break

    print("\n\nFontes:")
    for n, (documento, titulo, url_pdf, p1, p2, _, _) in enumerate(trechos, 1):
        print(f"  [{n}] {(titulo or documento)[:90]} ({formatar_paginas(p1, p2)})")
        if url_pdf:
            print(f"      {url_pdf}")


if __name__ == "__main__":
    main()