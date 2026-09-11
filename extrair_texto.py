#!/usr/bin/env python3
"""
Extrai o texto dos PDFs baixados e identifica quais precisam de OCR.

Instalação:
    python -m pip install pymupdf

Uso (na pasta onde está a pdfs_everyspec):
    python extrair_texto.py

Saída:
    textos_extraidos/<categoria>/<documento>.json   texto página a página + metadados
    textos_extraidos/relatorio_extracao.csv         resumo (abre no Excel)
"""

import csv
import json
import re
from pathlib import Path

import pymupdf

PASTA_PDFS = Path("pdfs_everyspec")
PASTA_SAIDA = Path("textos_extraidos")
MIN_CARACTERES = 50   # página com menos texto que isso é considerada "sem texto" (imagem)
LIMITE_OCR = 0.3      # se mais de 30% das páginas estão sem texto, o PDF é marcado para OCR

# Marca d'água que o EverySpec coloca em todas as páginas
RE_MARCA_DAGUA = re.compile(r"Downloaded\s+from\s+https?://(www\.)?everyspec\.com", re.I)


def carregar_manifesto():
    metadados = {}
    arquivo = PASTA_PDFS / "manifesto.jsonl"
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                reg = json.loads(linha)
                chave = reg["arquivo"].replace("\\", "/")
                metadados[chave] = reg
    return metadados


def limpar(texto):
    texto = RE_MARCA_DAGUA.sub("", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def processar(caminho, relativo, meta):
    paginas, sem_texto = [], []
    with pymupdf.open(caminho) as doc:
        for numero, pagina in enumerate(doc, start=1):
            texto = limpar(pagina.get_text("text"))
            if len(texto) < MIN_CARACTERES:
                sem_texto.append(numero)
            paginas.append({"numero": numero, "texto": texto})

    total = len(paginas)
    proporcao = len(sem_texto) / total if total else 1
    return {
        **meta,
        "arquivo": relativo,
        "total_paginas": total,
        "paginas_sem_texto": sem_texto,
        "precisa_ocr": proporcao > LIMITE_OCR,
        "paginas": paginas,
    }


def main():
    pdfs = sorted(PASTA_PDFS.rglob("*.pdf"))
    if not pdfs:
        print(f"Nenhum PDF encontrado em {PASTA_PDFS.resolve()}")
        return

    metadados = carregar_manifesto()
    relatorio = []
    print(f"Processando {len(pdfs)} PDFs...\n")

    for caminho in pdfs:
        relativo = caminho.relative_to(PASTA_PDFS).as_posix()
        meta = metadados.get(relativo, {"titulo": caminho.stem})
        try:
            resultado = processar(caminho, relativo, meta)
        except Exception as e:
            print(f"  ERRO        {relativo}: {e}")
            relatorio.append({"arquivo": relativo, "status": f"erro: {e}"})
            continue

        destino = PASTA_SAIDA / Path(relativo).with_suffix(".json")
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

        status = "PRECISA OCR" if resultado["precisa_ocr"] else "ok"
        n_sem = len(resultado["paginas_sem_texto"])
        print(f"  {status:<11} {resultado['total_paginas']:>4} págs ({n_sem} sem texto)  {relativo}")
        relatorio.append({
            "arquivo": relativo,
            "status": status,
            "total_paginas": resultado["total_paginas"],
            "paginas_sem_texto": n_sem,
            "titulo": resultado.get("titulo", ""),
        })

    PASTA_SAIDA.mkdir(exist_ok=True)
    caminho_csv = PASTA_SAIDA / "relatorio_extracao.csv"
    campos = ["arquivo", "status", "total_paginas", "paginas_sem_texto", "titulo"]
    with caminho_csv.open("w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.DictWriter(f, fieldnames=campos, delimiter=";", extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(relatorio)

    ok = sum(1 for r in relatorio if r["status"] == "ok")
    ocr = sum(1 for r in relatorio if r["status"] == "PRECISA OCR")
    erros = len(relatorio) - ok - ocr
    print(f"\nResumo: {ok} com texto | {ocr} precisam de OCR | {erros} com erro")
    print(f"Textos: {PASTA_SAIDA.resolve()}")
    print(f"Relatório: {caminho_csv.resolve()}")


if __name__ == "__main__":
    main()