"""
Gera os embeddings dos chunks com o Ollama e grava tudo no PostgreSQL (pgvector).

Pré-requisitos:
    ollama pull bge-m3                              (modelo de embedding)
    docker compose up -d                            (PostgreSQL com pgvector)
    python -m pip install "psycopg[binary]" requests

Uso:
    python gerar_embeddings.py --limite 50    teste rápido com 50 chunks
    python gerar_embeddings.py                todos os chunks (pode interromper com Ctrl+C e continuar)

Variáveis de ambiente opcionais:
    DATABASE_URL   padrão: postgresql://rag:rag@localhost:5432/rag
    OLLAMA_URL     padrão: http://localhost:11434
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import psycopg
import requests

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
ARQUIVO_CHUNKS = Path("chunks/chunks.jsonl")
ARQUIVO_FALHAS = Path("chunks/falhas_embedding.jsonl")

SQL_CRIAR_TABELA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS rag_chunks (
    id            TEXT PRIMARY KEY,
    documento     TEXT NOT NULL,
    titulo        TEXT,
    categoria     TEXT,
    url_pdf       TEXT,
    pagina_inicio INTEGER,
    pagina_fim    INTEGER,
    chunk         INTEGER,
    qualidade     REAL,
    texto         TEXT NOT NULL,
    modelo        TEXT NOT NULL,
    embedding     vector({dim}) NOT NULL,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_chunks_documento_idx ON rag_chunks (documento);
"""

SQL_INSERIR = """
INSERT INTO rag_chunks (id, documento, titulo, categoria, url_pdf, pagina_inicio, pagina_fim,
                        chunk, qualidade, texto, modelo, embedding)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
ON CONFLICT (id) DO NOTHING
"""


class ErroOllama(Exception):
    pass


def gerar_vetores(textos, modelo):
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": modelo, "input": textos},
            timeout=300,
        )
    except requests.ConnectionError:
        sys.exit(
            f"\nNão consegui conectar ao Ollama em {OLLAMA_URL}. Confira se ele está aberto."
        )
    if r.status_code != 200:
        raise ErroOllama(f"HTTP {r.status_code}: {r.text[:300]}")
    vetores = r.json().get("embeddings", [])
    if len(vetores) != len(textos):
        raise ErroOllama(
            "o Ollama devolveu uma quantidade de vetores diferente da enviada"
        )
    for v in vetores:
        if not all(math.isfinite(x) for x in v):
            raise ErroOllama("vetor com valores inválidos (NaN)")
    return vetores


def processar_lote(lote, modelo):
    """Tenta o lote inteiro; se falhar, tenta chunk por chunk para isolar o problemático."""
    try:
        return list(zip(lote, gerar_vetores([c["texto"] for c in lote], modelo))), []
    except ErroOllama as erro_lote:
        if len(lote) == 1:
            return [], [(lote[0], str(erro_lote))]

    ok, falhas = [], []
    for c in lote:
        try:
            ok.append((c, gerar_vetores([c["texto"]], modelo)[0]))
        except ErroOllama as e:
            falhas.append((c, str(e)))
    if not ok:
        # Se nenhum chunk do lote funcionou, o problema não é o texto: é o Ollama
        sys.exit(
            f"\nO Ollama falhou em todos os chunks do lote. Último erro:\n{falhas[-1][1]}"
        )
    return ok, falhas


def linha_banco(c, vetor, modelo):
    return (
        c["id"],
        c["documento"],
        c.get("titulo"),
        c.get("categoria"),
        c.get("url_pdf"),
        c.get("pagina_inicio"),
        c.get("pagina_fim"),
        c.get("chunk"),
        c.get("qualidade"),
        c["texto"],
        modelo,
        "[" + ",".join(f"{x:.7g}" for x in vetor) + "]",
    )


def formatar_tempo(segundos):
    minutos = int(segundos // 60)
    return (
        f"{minutos // 60}h{minutos % 60:02d}min"
        if minutos >= 60
        else f"{minutos}min{int(segundos % 60):02d}s"
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Gera embeddings dos chunks e grava no PostgreSQL."
    )
    ap.add_argument("--modelo", default="bge-m3", help="modelo de embedding do Ollama")
    ap.add_argument(
        "--lote", type=int, default=16, help="chunks enviados ao Ollama por vez"
    )
    ap.add_argument(
        "--limite", type=int, default=0, help="processa só N chunks novos (0 = todos)"
    )
    ap.add_argument(
        "--recriar",
        action="store_true",
        help="APAGA a tabela rag_chunks e começa do zero (use ao trocar de modelo)",
    )
    args = ap.parse_args()

    if not ARQUIVO_CHUNKS.exists():
        sys.exit(
            f"Arquivo {ARQUIVO_CHUNKS} não encontrado. Rode antes o gerar_chunks.py"
        )

    # 1. Ollama
    try:
        dimensao = len(gerar_vetores(["teste"], args.modelo)[0])
    except ErroOllama as e:
        sys.exit(
            f"O Ollama respondeu com erro: {e}\nConfira se o modelo está baixado com: ollama list"
        )
    print(f"Ollama ok: modelo {args.modelo}, vetores de {dimensao} dimensões")

    # 2. PostgreSQL
    try:
        conn = psycopg.connect(DATABASE_URL)
    except psycopg.OperationalError as e:
        sys.exit(
            f"Não consegui conectar ao PostgreSQL.\nO container está no ar? Rode: docker compose ps\nDetalhe: {e}"
        )

    try:
        if args.recriar:
            conn.execute("DROP TABLE IF EXISTS rag_chunks")
            print("Tabela rag_chunks apagada (--recriar)")
        conn.execute(SQL_CRIAR_TABELA.format(dim=dimensao))
        conn.commit()

        modelos = [
            linha[0] for linha in conn.execute("SELECT DISTINCT modelo FROM rag_chunks")
        ]
        if modelos and modelos != [args.modelo]:
            sys.exit(
                f"A tabela já tem embeddings do modelo {modelos}. Para trocar para {args.modelo}, "
                f"rode com --recriar (isso apaga os embeddings atuais)."
            )
        ja_gravados = {linha[0] for linha in conn.execute("SELECT id FROM rag_chunks")}
        print(f"PostgreSQL ok: {len(ja_gravados)} chunks já estavam no banco")

        # 3. Chunks pendentes
        with ARQUIVO_CHUNKS.open(encoding="utf-8") as f:
            todos = [json.loads(linha) for linha in f if linha.strip()]
        pendentes = [c for c in todos if c["id"] not in ja_gravados]
        if args.limite:
            pendentes = pendentes[: args.limite]
        total = len(pendentes)
        print(f"Chunks para processar agora: {total}\n")

        # 4. Embeddings em lotes
        falhas_registradas = set()
        if ARQUIVO_FALHAS.exists():
            with ARQUIVO_FALHAS.open(encoding="utf-8") as f:
                falhas_registradas = {
                    json.loads(linha)["id"] for linha in f if linha.strip()
                }
        gravados = falhas_total = 0
        inicio = time.time()
        for i in range(0, total, args.lote):
            lote = pendentes[i : i + args.lote]
            ok, falhas = processar_lote(lote, args.modelo)

            if ok:
                with conn.cursor() as cur:
                    cur.executemany(
                        SQL_INSERIR, [linha_banco(c, v, args.modelo) for c, v in ok]
                    )
                conn.commit()
                gravados += len(ok)

            if falhas:
                falhas_total += len(falhas)
                with ARQUIVO_FALHAS.open("a", encoding="utf-8") as f:
                    for c, erro in falhas:
                        print(f"  ! chunk ignorado: {c['id']} ({erro[:80]})")
                        if c["id"] not in falhas_registradas:
                            f.write(
                                json.dumps(
                                    {"id": c["id"], "erro": erro}, ensure_ascii=False
                                )
                                + "\n"
                            )
                            falhas_registradas.add(c["id"])

            feitos = gravados + falhas_total
            if feitos == total or (i // args.lote) % 10 == 0:
                decorrido = time.time() - inicio
                taxa = feitos / decorrido if decorrido else 0
                restante = (total - feitos) / taxa if taxa else 0
                print(
                    f"  {feitos}/{total} ({feitos / total:.0%}) | {taxa:.1f} chunks/s | "
                    f"decorrido {formatar_tempo(decorrido)} | faltam ~{formatar_tempo(restante)}"
                )

        # 5. Índice de busca (só quando todos os chunks do arquivo estiverem no banco)
        no_banco = conn.execute("SELECT count(*) FROM rag_chunks").fetchone()[0]
        faltando = len(todos) - no_banco - falhas_total
        indice_existe = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'rag_chunks_embedding_idx'"
        ).fetchone()
        if faltando <= 0 and not indice_existe:
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
        elif faltando <= 0:
            print("\nÍndice de busca já existe.")
        else:
            print(
                f"\nAinda faltam {faltando} chunks. O índice será criado quando todos estiverem no banco."
            )

        print(
            f"\nResumo: {gravados} gravados agora | {falhas_total} com falha | {no_banco} no banco no total"
        )
        if falhas_total:
            print(f"Falhas registradas em: {ARQUIVO_FALHAS.resolve()}")

    except KeyboardInterrupt:
        print(
            "\nInterrompido. O que já foi gravado está salvo; rode de novo para continuar."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
