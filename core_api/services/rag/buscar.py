"""
Testa a busca vetorial: mostra os chunks mais parecidos com uma pergunta (ainda sem LLM).

Uso:
    python buscar.py "qual a umidade para condicionar corpos de prova?"
    python buscar.py "requisitos de abortar a missão da Orion" --k 10
"""

import argparse
import os
import sys
import textwrap

import psycopg
import requests

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Busca os chunks mais parecidos com uma pergunta."
    )
    ap.add_argument("pergunta")
    ap.add_argument("--k", type=int, default=5, help="quantos chunks mostrar")
    ap.add_argument("--modelo", default="bge-m3")
    args = ap.parse_args()

    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": args.modelo, "input": [args.pergunta]},
            timeout=120,
        )
    except requests.ConnectionError:
        sys.exit(
            f"Não consegui conectar ao Ollama em {OLLAMA_URL}. Confira se ele está aberto."
        )
    if r.status_code != 200:
        sys.exit(f"Erro do Ollama: {r.text[:300]}")
    vetor = "[" + ",".join(f"{x:.7g}" for x in r.json()["embeddings"][0]) + "]"

    with psycopg.connect(DATABASE_URL) as conn:
        linhas = conn.execute(
            """
            SELECT documento, titulo, pagina_inicio, pagina_fim, texto,
                   1 - (embedding <=> %(vetor)s::vector) AS similaridade
            FROM rag_chunks
            ORDER BY embedding <=> %(vetor)s::vector
            LIMIT %(k)s
            """,
            {"vetor": vetor, "k": args.k},
        ).fetchall()

    if not linhas:
        sys.exit("Nenhum chunk no banco. Rode antes o gerar_embeddings.py")

    print(f'Pergunta: "{args.pergunta}"')
    for n, (documento, titulo, p1, p2, texto, similaridade) in enumerate(linhas, 1):
        paginas = f"p. {p1}" if p1 == p2 else f"p. {p1}-{p2}"
        print(f"\n[{n}] similaridade {similaridade:.3f} | {documento} ({paginas})")
        print(f"    {(titulo or '')[:110]}")
        print(
            textwrap.indent(textwrap.fill(" ".join(texto.split())[:450], 100), "    ")
        )


if __name__ == "__main__":
    main()
