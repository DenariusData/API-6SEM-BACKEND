"""
Responde perguntas com base nos documentos (RAG): busca os trechos mais parecidos no PostgreSQL
e pede ao LLM do Ollama uma resposta em português, citando de onde veio cada informação.

Pré-requisitos:
    ollama pull llama3.2

Os trechos vêm da mesma busca da API (busca_vetorial.py, AKA-51): só documentos
disponíveis, mais a base pública do EverySpec.

Uso:
    python responder.py "Como é feito o ensaio de tração em materiais compósitos?"
    python responder.py "Quanto tempo a Orion deve manter a tripulação viva sem pressurização?" --mostrar-trechos
"""

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

REGRAS_IDIOMA = {
    "pt-BR": "Responda sempre em português do Brasil, mesmo que os trechos estejam em inglês.",
    "en": "Answer always in English, even if the excerpts or question are in Portuguese or another language.",
}

FRASES_NAO_ENCONTROU = {
    "pt-BR": "Não encontrei essa informação nos documentos consultados.",
    "en": "I did not find this information in the consulted documents.",
}

PROMPTS_SISTEMA = {
    "pt-BR": """Você é um assistente técnico que responde perguntas sobre normas e especificações técnicas.

Regras:
- Responda sempre em português do Brasil, mesmo que os trechos estejam em inglês.
- Use SOMENTE as informações dos trechos fornecidos. Não use conhecimento próprio.
- Se os trechos não tiverem a resposta, diga: "Não encontrei essa informação nos documentos consultados."
- Indique a fonte de cada informação com o número do trecho entre colchetes, por exemplo [1] ou [2].
- Mantenha siglas, códigos e números exatamente como no original (ex.: CA0532-PO, ASTM D3039, 144 horas).
- Trechos citados devem permanecer no idioma original do documento.
- Seja objetivo.""",
    "en": """You are a technical assistant that answers questions about technical standards and specifications.

Rules:
- Answer always in English, even if the excerpts or question are in Portuguese or another language.
- Use ONLY the information from the provided excerpts. Do not use your own knowledge.
- If the excerpts do not have the answer, say: "I did not find this information in the consulted documents."
- Indicate the source of each information with the excerpt number in brackets, for example [1] or [2].
- Keep acronyms, codes and numbers exactly as in the original (e.g.: CA0532-PO, ASTM D3039, 144 hours).
- Cited excerpts must remain in the original language of the document.
- Be objective.""",
}


def montar_prompt_sistema(idioma="pt-BR"):
    """Monta o prompt do sistema para o idioma escolhido."""
    return PROMPTS_SISTEMA.get(idioma, PROMPTS_SISTEMA["pt-BR"])


PROMPT_SISTEMA = montar_prompt_sistema("pt-BR")


def ollama(caminho, dados, **kwargs):
    try:
        r = requests.post(f"{OLLAMA_URL}{caminho}", json=dados, timeout=600, **kwargs)
    except requests.ConnectionError:
        sys.exit(
            f"\nNão consegui conectar ao Ollama em {OLLAMA_URL}. Confira se ele está aberto."
        )
    if r.status_code != 200:
        sys.exit(
            f"\nErro do Ollama ({r.status_code}): {r.text[:300]}\n"
            f"Se o modelo não foi encontrado, baixe com: ollama pull {dados.get('model')}"
        )
    return r


def buscar_trechos(pergunta, k, modelo_embedding):
    """Sobe o Django (o script roda fora do manage.py) e usa a busca da API.
    O import fica aqui dentro porque a API importa este módulo (prompts)."""
    import django

    resposta = ollama("/api/embed", {"model": modelo_embedding, "input": [pergunta]})
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")
    django.setup()
    from core_api.services.rag.busca_vetorial import buscar_por_vetor

    return buscar_por_vetor(resposta.json()["embeddings"][0], k)


def formatar_paginas(p1, p2):
    return f"p. {p1}" if p1 == p2 else f"p. {p1}-{p2}"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Responde perguntas com base nos documentos."
    )
    ap.add_argument("pergunta")
    ap.add_argument("--k", type=int, default=5, help="quantos trechos enviar ao LLM")
    ap.add_argument(
        "--modelo", default="llama3.2", help="LLM do Ollama que escreve a resposta"
    )
    ap.add_argument("--modelo-embedding", default="bge-m3")
    ap.add_argument(
        "--mostrar-trechos",
        action="store_true",
        help="mostra os trechos enviados ao LLM",
    )
    ap.add_argument(
        "--idioma",
        default="pt-BR",
        choices=["pt-BR", "en"],
        help="idioma da resposta (pt-BR ou en)",
    )
    args = ap.parse_args()

    trechos = buscar_trechos(args.pergunta, args.k, args.modelo_embedding)
    if not trechos:
        sys.exit(
            "Nenhum trecho encontrado. Aprove um documento ou rode o gerar_embeddings.py"
        )

    contexto = "\n\n".join(
        f"[{n}] Documento: {t['titulo'] or t['documento']} | "
        f"{formatar_paginas(t['pagina_inicio'], t['pagina_fim'])}\n"
        f"{' '.join(t['texto'].split())}"
        for n, t in enumerate(trechos, 1)
    )

    if args.mostrar_trechos:
        print("=== Trechos enviados ao LLM ===")
        for n, t in enumerate(trechos, 1):
            paginas = formatar_paginas(t["pagina_inicio"], t["pagina_fim"])
            print(
                f"\n[{n}] similaridade {t['similaridade']:.3f} | "
                f"{t['documento']} ({paginas})"
            )
            print(
                textwrap.indent(
                    textwrap.fill(" ".join(t["texto"].split())[:300], 100), "    "
                )
            )
        print()

    print(f'Pergunta: "{args.pergunta}"\n')
    print("Resposta: ", end="", flush=True)

    mensagens = [
        {"role": "system", "content": montar_prompt_sistema(args.idioma)},
        {
            "role": "user",
            "content": f"Trechos dos documentos:\n\n{contexto}\n\nPergunta: {args.pergunta}",
        },
    ]
    resposta = ollama(
        "/api/chat",
        {
            "model": args.modelo,
            "messages": mensagens,
            "stream": True,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        },
        stream=True,
    )

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
    for n, t in enumerate(trechos, 1):
        paginas = formatar_paginas(t["pagina_inicio"], t["pagina_fim"])
        print(f"  [{n}] {(t['titulo'] or t['documento'])[:90]} ({paginas})")
        if t["url_pdf"]:
            print(f"      {t['url_pdf']}")


if __name__ == "__main__":
    main()
