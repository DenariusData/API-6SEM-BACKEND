#!/usr/bin/env python3
"""
Divide os textos extraídos em pedaços (chunks) para o RAG, descartando texto ilegível.

Instalação:
    python -m pip install wordfreq

Uso (na pasta onde está a textos_extraidos):
    python gerar_chunks.py
    python gerar_chunks.py --qualidade-min 0.8 --excluir MIL_HDBK_103AA

Saída:
    chunks/chunks.jsonl       chunks aprovados (texto, páginas e metadados do documento)
    chunks/descartados.jsonl  chunks reprovados no filtro de qualidade, para conferência
"""

import argparse
import json
import re
import sys
from functools import cache
from pathlib import Path

try:
    from wordfreq import zipf_frequency
except ImportError:
    sys.exit("Falta a biblioteca wordfreq. Rode: python -m pip install wordfreq")

PASTA_TEXTOS = Path("textos_extraidos")
PASTA_SAIDA = Path("chunks")
MIN_CARACTERES_PAGINA = 50   # páginas com menos texto que isso (digitalizadas) são ignoradas
MIN_CARACTERES_CHUNK = 100   # chunks muito pequenos são descartados
MIN_PALAVRAS_AVALIAR = 20    # com menos palavras que isso, o filtro de qualidade não opina


# ----------------------------------------------------------------------------- qualidade
@cache
def palavra_valida(palavra):
    if palavra.isupper():                      # siglas: MIL, HDBK, NASA, CEV...
        return True
    return zipf_frequency(palavra.lower(), "en") >= 1.5


def qualidade(texto):
    """Fração das palavras que existem em inglês. OCR ruim gera muitas palavras inexistentes."""
    total = validas = 0
    for token in texto.split():
        for parte in re.split(r"[-/,]", token):
            parte = parte.strip(".;:()[]{}\"'!?*")
            if len(parte) < 2 or any(c.isdigit() for c in parte):
                continue                        # números, códigos e letras soltas não contam
            total += 1
            if parte.isalpha() and palavra_valida(parte):
                validas += 1
    if total < MIN_PALAVRAS_AVALIAR:
        return None
    return validas / total


# ----------------------------------------------------------------------------- chunking
def limpar_pagina(texto):
    texto = re.sub(r"\.{4,}", " ", texto)                 # pontilhado de sumário "Scope ........ 1"
    texto = re.sub(r"([a-z])-\n([a-z])", r"\1\2", texto)  # palavra hifenizada na quebra de linha
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto


def linhas_do_documento(doc, tamanho):
    """Gera (linha, número da página), quebrando linhas maiores que o tamanho do chunk."""
    for pagina in doc["paginas"]:
        if len(pagina["texto"]) < MIN_CARACTERES_PAGINA:
            continue
        for linha in limpar_pagina(pagina["texto"]).splitlines():
            linha = linha.strip()
            while len(linha) > tamanho:
                corte = linha.rfind(" ", 0, tamanho)
                corte = corte if corte > 0 else tamanho
                yield linha[:corte], pagina["numero"]
                linha = linha[corte:].strip()
            if linha:
                yield linha, pagina["numero"]


def dividir(doc, tamanho, sobreposicao):
    chunks = []
    atual = []          # lista de (linha, página) do chunk em construção
    tamanho_atual = 0
    tem_novidade = False

    def fechar():
        texto = "\n".join(linha for linha, _ in atual)
        if len(texto) >= MIN_CARACTERES_CHUNK:
            chunks.append({
                "texto": texto,
                "pagina_inicio": atual[0][1],
                "pagina_fim": atual[-1][1],
            })

    for linha, pagina in linhas_do_documento(doc, tamanho):
        if atual and tamanho_atual + len(linha) + 1 > tamanho:
            fechar()
            # Sobreposição: o próximo chunk começa com as últimas linhas deste
            mantidas, soma = [], 0
            for l, p in reversed(atual):
                if soma + len(l) + 1 > sobreposicao:
                    break
                mantidas.insert(0, (l, p))
                soma += len(l) + 1
            if soma + len(linha) + 1 > tamanho:   # linha nova muito grande: sem sobreposição
                mantidas, soma = [], 0
            atual, tamanho_atual = mantidas, soma
            tem_novidade = False
        atual.append((linha, pagina))
        tamanho_atual += len(linha) + 1
        tem_novidade = True

    if atual and tem_novidade:
        fechar()
    return chunks


# ----------------------------------------------------------------------------- principal
def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Divide os textos extraídos em chunks para o RAG.")
    ap.add_argument("--tamanho", type=int, default=1500,
                    help="tamanho máximo de cada chunk em caracteres (1500 = cerca de 350 tokens em inglês)")
    ap.add_argument("--sobreposicao", type=int, default=200,
                    help="caracteres repetidos entre um chunk e o seguinte")
    ap.add_argument("--qualidade-min", type=float, default=0.8,
                    help="fração mínima de palavras reconhecidas em inglês (0 a 1); 0 desliga o filtro")
    ap.add_argument("--excluir", nargs="*", default=[],
                    help="trechos do nome de arquivos a ignorar (ex.: MIL_HDBK_103AA)")
    args = ap.parse_args()

    arquivos = sorted(PASTA_TEXTOS.rglob("*.json"))
    if not arquivos:
        print(f"Nenhum texto encontrado em {PASTA_TEXTOS.resolve()}. Rode antes o extrair_texto.py")
        return

    PASTA_SAIDA.mkdir(exist_ok=True)
    caminho_saida = PASTA_SAIDA / "chunks.jsonl"
    caminho_descartados = PASTA_SAIDA / "descartados.jsonl"
    excluir = [e.lower() for e in args.excluir]

    tamanhos, notas = [], []
    sem_chunks, excluidos = [], []
    total_descartados = 0
    exemplo_ok = exemplo_ruim = None
    print(f"Dividindo {len(arquivos)} documentos (qualidade mínima {args.qualidade_min:.0%})...\n")
    print(f"  {'aprovados':>9} {'descartados':>11}  documento")

    with caminho_saida.open("w", encoding="utf-8") as saida, \
         caminho_descartados.open("w", encoding="utf-8") as descartados:
        for caminho in arquivos:
            doc = json.loads(caminho.read_text(encoding="utf-8"))
            arquivo = doc.get("arquivo") or caminho.relative_to(PASTA_TEXTOS).with_suffix(".pdf").as_posix()
            if any(e in arquivo.lower() for e in excluir):
                excluidos.append(arquivo)
                continue

            pedacos = dividir(doc, args.tamanho, args.sobreposicao)
            if not pedacos:
                sem_chunks.append(arquivo)
                continue

            base = Path(arquivo).with_suffix("").as_posix()
            aprovados = reprovados = 0
            for i, pedaco in enumerate(pedacos):
                nota = qualidade(pedaco["texto"])
                registro = {
                    "id": f"{base}-{i:04d}",
                    "documento": arquivo,
                    "titulo": doc.get("titulo", ""),
                    "categoria": doc.get("categoria", ""),
                    "url_pdf": doc.get("url_pdf", ""),
                    "pagina_inicio": pedaco["pagina_inicio"],
                    "pagina_fim": pedaco["pagina_fim"],
                    "chunk": i,
                    "qualidade": None if nota is None else round(nota, 3),
                    "texto": pedaco["texto"],
                }
                linha_json = json.dumps(registro, ensure_ascii=False) + "\n"
                if nota is not None and nota < args.qualidade_min:
                    descartados.write(linha_json)
                    reprovados += 1
                    if exemplo_ruim is None or abs(nota - args.qualidade_min) < abs(exemplo_ruim["qualidade"] - args.qualidade_min):
                        exemplo_ruim = registro   # guarda o descartado mais perto do limite
                    continue
                saida.write(linha_json)
                aprovados += 1
                tamanhos.append(len(pedaco["texto"]))
                if nota is not None:
                    notas.append(nota)
                if exemplo_ok is None and i == 2:
                    exemplo_ok = registro
            total_descartados += reprovados
            print(f"  {aprovados:>9} {reprovados:>11}  {arquivo}")

    print(f"\nResumo: {len(tamanhos)} chunks aprovados | {total_descartados} descartados por qualidade")
    if tamanhos:
        print(f"Tamanho dos aprovados: média {sum(tamanhos) // len(tamanhos)} | "
              f"mínimo {min(tamanhos)} | máximo {max(tamanhos)} caracteres")
    if notas:
        faixas = [(0.8, 0.9), (0.9, 0.95), (0.95, 1.01)]
        texto_faixas = " | ".join(
            f"{a:.0%}-{min(b, 1):.0%}: {sum(1 for n in notas if a <= n < b)}" for a, b in faixas)
        print(f"Qualidade dos aprovados: {texto_faixas}")
    if excluidos:
        print(f"Excluídos pelo --excluir: {len(excluidos)}")
        for arquivo in excluidos:
            print(f"  - {arquivo}")
    if sem_chunks:
        print(f"Sem nenhum texto aproveitável: {len(sem_chunks)}")
        for arquivo in sem_chunks:
            print(f"  - {arquivo}")
    print(f"Aprovados: {caminho_saida.resolve()}")
    print(f"Descartados: {caminho_descartados.resolve()}")

    for rotulo, exemplo in [("APROVADO", exemplo_ok), ("DESCARTADO mais perto do limite", exemplo_ruim)]:
        if exemplo:
            print(f"\n--- Exemplo {rotulo} ---")
            print(f"id: {exemplo['id']} | páginas {exemplo['pagina_inicio']}-{exemplo['pagina_fim']} "
                  f"| qualidade {exemplo['qualidade']}")
            print(exemplo["texto"][:500])


if __name__ == "__main__":
    main()