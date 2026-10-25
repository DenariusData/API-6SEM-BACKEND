"""
Testa a busca vetorial: mostra os chunks mais parecidos com uma pergunta (ainda sem LLM).

Usa a mesma busca da API (busca_vetorial.py, AKA-51): só documentos disponíveis,
mais a base pública do EverySpec.

Uso:
    python buscar.py "qual a umidade para condicionar corpos de prova?"
    python buscar.py "requisitos de abortar a missão da Orion" --k 10
"""

import argparse
import os
import sys
import textwrap
from pathlib import Path

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


def buscar_com_django(vetor, k, modelo):
    """Sobe o Django (o script roda fora do manage.py) e usa a busca da API,
    para o filtro de documentos disponíveis ficar num lugar só."""
    import django

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")
    django.setup()
    from core_api.services.rag.busca_vetorial import buscar_por_vetor

    return buscar_por_vetor(vetor, k, modelo)


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
    trechos = buscar_com_django(r.json()["embeddings"][0], args.k, args.modelo)

    if not trechos:
        sys.exit(
            "Nenhum trecho encontrado. Aprove um documento ou rode o gerar_embeddings.py"
        )

    print(f'Pergunta: "{args.pergunta}"')
    for n, t in enumerate(trechos, 1):
        p1, p2 = t["pagina_inicio"], t["pagina_fim"]
        if p1 is None:
            paginas = "p. —"
        else:
            paginas = f"p. {p1}" if p1 == p2 or p2 is None else f"p. {p1}-{p2}"
        origem = "sistema" if t["documento_id"] else "base pública"
        print(
            f"\n[{n}] similaridade {t['similaridade']:.3f} | {t['documento']} "
            f"({paginas}) [{origem}]"
        )
        print(f"    {(t['titulo'] or '')[:110]}")
        print(
            textwrap.indent(
                textwrap.fill(" ".join(t["texto"].split())[:450], 100), "    "
            )
        )


if __name__ == "__main__":
    main()
