#!/usr/bin/env python3
"""
Importa no PostgreSQL os embeddings gerados em outra máquina (por exemplo, no Google Colab).

Antes de gravar, confere se os vetores importados batem com os do Ollama local.
Isso é essencial: as perguntas do buscar.py são convertidas pelo Ollama, então
os chunks precisam estar no mesmo "mapa de significados".

Uso:
    python -m pip install numpy
    python importar_embeddings.py embeddings_bge_m3.npz
"""

import argparse
import json
import random
import sys
import time

import numpy as np
import psycopg

from gerar_embeddings import (
    ARQUIVO_CHUNKS,
    DATABASE_URL,
    SQL_CRIAR_TABELA,
    SQL_INSERIR,
    ErroOllama,
    gerar_vetores,
    linha_banco,
)

SIMILARIDADE_MINIMA = 0.98  # vetores do mesmo modelo devem ser praticamente iguais
AMOSTRAS = 5
TAMANHO_LOTE = 500


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Importa embeddings gerados fora desta máquina."
    )
    ap.add_argument("arquivo", help="arquivo .npz baixado do Colab")
    ap.add_argument("--modelo", default="bge-m3", help="nome do modelo no Ollama local")
    args = ap.parse_args()

    # 1. Arquivo importado
    dados = np.load(args.arquivo)
    ids = [str(i) for i in dados["ids"]]
    vetores = dados["vetores"].astype(np.float32)
    if len(ids) != len(vetores):
        sys.exit("Arquivo inválido: quantidade de ids diferente da de vetores.")
    if not np.isfinite(vetores).all():
        sys.exit("Arquivo inválido: há vetores com NaN ou infinito.")
    dimensao = vetores.shape[1]
    print(f"Arquivo ok: {len(ids)} vetores de {dimensao} dimensões")

    # 2. Metadados dos chunks
    with ARQUIVO_CHUNKS.open(encoding="utf-8") as f:
        chunks = {
            c["id"]: c for c in (json.loads(linha) for linha in f if linha.strip())
        }
    sem_chunk = [i for i in ids if i not in chunks]
    if sem_chunk:
        sys.exit(
            f"{len(sem_chunk)} ids do arquivo não existem no {ARQUIVO_CHUNKS} "
            f"(ex.: {sem_chunk[0]}). Os chunks foram gerados de novo depois do Colab?"
        )

    # 3. Compatibilidade com o Ollama local
    print(f"\nConferindo compatibilidade com o Ollama ({AMOSTRAS} amostras)...")
    posicoes = random.sample(range(len(ids)), min(AMOSTRAS, len(ids)))
    try:
        locais = gerar_vetores([chunks[ids[p]]["texto"] for p in posicoes], args.modelo)
    except ErroOllama as e:
        sys.exit(f"O Ollama respondeu com erro: {e}")
    similaridades = []
    for p, local in zip(posicoes, locais):
        a = vetores[p] / np.linalg.norm(vetores[p])
        b = np.asarray(local, dtype=np.float32)
        b = b / np.linalg.norm(b)
        similaridades.append(float(a @ b))
        print(f"  {ids[p]}: similaridade {similaridades[-1]:.4f}")
    if min(similaridades) < SIMILARIDADE_MINIMA:
        sys.exit(
            f"\nOs vetores importados NÃO batem com os do Ollama (mínimo {min(similaridades):.4f}, "
            f"esperado >= {SIMILARIDADE_MINIMA}).\nO modelo do Colab provavelmente é diferente. Nada foi gravado."
        )
    print("Compatível: os vetores são equivalentes aos do Ollama local.")

    # 4. Gravação no PostgreSQL
    try:
        conn = psycopg.connect(DATABASE_URL)
    except psycopg.OperationalError as e:
        sys.exit(
            f"Não consegui conectar ao PostgreSQL.\nO container está no ar? Rode: docker compose ps\nDetalhe: {e}"
        )

    try:
        conn.execute(SQL_CRIAR_TABELA.format(dim=dimensao))
        conn.commit()
        modelos = [
            linha[0] for linha in conn.execute("SELECT DISTINCT modelo FROM rag_chunks")
        ]
        if modelos and modelos != [args.modelo]:
            sys.exit(
                f"A tabela já tem embeddings do modelo {modelos}, diferente de {args.modelo}."
            )
        ja_gravados = {linha[0] for linha in conn.execute("SELECT id FROM rag_chunks")}
        pendentes = [p for p, i in enumerate(ids) if i not in ja_gravados]
        print(
            f"\nPostgreSQL ok: {len(ja_gravados)} já estavam no banco, {len(pendentes)} para gravar"
        )

        inicio = time.time()
        for n in range(0, len(pendentes), TAMANHO_LOTE):
            lote = pendentes[n : n + TAMANHO_LOTE]
            with conn.cursor() as cur:
                cur.executemany(
                    SQL_INSERIR,
                    [
                        linha_banco(chunks[ids[p]], vetores[p].tolist(), args.modelo)
                        for p in lote
                    ],
                )
            conn.commit()
            feitos = n + len(lote)
            print(
                f"  {feitos}/{len(pendentes)} ({feitos / len(pendentes):.0%}) | {time.time() - inicio:.0f}s"
            )

        no_banco = conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0]
        indice_existe = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'rag_chunks_embedding_idx'"
        ).fetchone()
        if not indice_existe:
            print(
                "\nCriando índice HNSW para a busca vetorial (pode levar alguns minutos)..."
            )
            conn.execute("SET maintenance_work_mem = '512MB'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx "
                "ON rag_chunks USING hnsw (embedding vector_cosine_ops)"
            )
            conn.commit()
            print("Índice pronto.")
        print(
            f"\nResumo: {len(pendentes)} gravados agora | {no_banco} no banco no total"
        )
    except KeyboardInterrupt:
        print(
            "\nInterrompido. O que já foi gravado está salvo; rode de novo para continuar."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
