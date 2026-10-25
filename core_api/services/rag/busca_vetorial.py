"""
Busca vetorial dos trechos que a IA pode usar (AKA-51 / S2-37).

Duas fontes, juntadas e ordenadas pela similaridade com a pergunta:

    1. Documentos do sistema (upload -> validação -> processamento): tabelas
       `chunk` + `embedding_version`. Só entram documentos de
       `Documento.objects.disponiveis()`. Esse é o ÚNICO lugar do filtro de
       documento.

    2. Base pública do EverySpec: tabela `rag_chunks`, gerada pelos comandos
       gerar_embeddings/importar_embeddings. São normas públicas, que não
       passam pelo fluxo de validação, então não têm Documento nem filtro.
       Os comandos gravam pelo DATABASE_URL, que precisa apontar para o mesmo
       banco do Django (o padrão dos dois é localhost:5434/rag). Se a tabela
       não existir nesse banco, a busca segue só com os documentos do sistema
       e registra um aviso no log.

Nas duas fontes só entram vetores do mesmo `modelo` da pergunta: vetores de
modelos diferentes têm tamanhos ou "espaços" diferentes e não se comparam.

Índice HNSW: o Postgres percorre o índice pela distância e só depois aplica o
filtro (disponível, modelo). Sem ajuste, o índice entrega só os 40 vizinhos
mais próximos (hnsw.ef_search), e se a maioria for de documentos não
disponíveis a busca volta com menos de k trechos, ou vazia. Por isso a busca
liga `hnsw.iterative_scan = relaxed_order` (pgvector 0.8+), que continua
percorrendo o índice até achar trechos que passem no filtro. Nesse modo a
ordem que vem do índice é aproximada, então cada fonte pede FATOR_CANDIDATOS
vezes k candidatos e a ordem final é refeita aqui, pela similaridade (como o
pgvector recomenda). `strict_order` também resolve, mas mediu ~10x mais lento
no p95. Em pgvector antigo, a busca aumenta o `hnsw.ef_search` como paliativo.

Sprint 3 (permissão por usuário): a regra entra em `disponiveis()`, mas a
busca vai precisar saber quem pergunta, então `buscar_por_vetor` deve ganhar
um parâmetro `usuario` nessa hora.

Cada trecho volta como dict:

    {
        "documento_id": str | None,  # id do Documento; None na base pública
        "documento": str,            # identificador do documento
        "revisao": str | None,       # só documentos do sistema
        "titulo": str,
        "url_pdf": str | None,       # só base pública
        "pagina_inicio": int | None,
        "pagina_fim": int | None,
        "texto": str,
        "similaridade": float,       # 1 - distância de cosseno
    }
"""

import logging

from django.db import DatabaseError, ProgrammingError, connection, transaction
from pgvector.django import CosineDistance

from core_api.models import Documento, EmbeddingVersion

logger = logging.getLogger(__name__)

TABELA_BASE_PUBLICA = "rag_chunks"
# Paliativo para pgvector sem busca iterativa (< 0.8): mais candidatos do índice.
EF_SEARCH_SEM_ITERATIVA = 200
# Candidatos por fonte, em múltiplos de k, para refazer a ordem aqui.
FATOR_CANDIDATOS = 4

SQL_BASE_PUBLICA = f"""
    SELECT documento, titulo, url_pdf, pagina_inicio, pagina_fim, texto,
           1 - (embedding <=> %s::vector) AS similaridade
    FROM {TABELA_BASE_PUBLICA}
    WHERE modelo = %s
    ORDER BY embedding <=> %s::vector
    LIMIT %s
"""

_avisos_dados = set()


def _avisar_uma_vez(chave, mensagem, *args):
    """Aviso no log só na primeira vez por processo, para não repetir a cada
    pergunta."""
    if chave not in _avisos_dados:
        _avisos_dados.add(chave)
        logger.warning(mensagem, *args)


def _vetor_sql(vetor) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vetor) + "]"


def _configurar_indice():
    """Liga a busca iterativa do HNSW só nesta transação (SET LOCAL)."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        return
    except DatabaseError:
        _avisar_uma_vez(
            "iterativa",
            "pgvector sem hnsw.iterative_scan (precisa da versão 0.8+); usando "
            "hnsw.ef_search = %s, que reduz mas não elimina o risco de a busca "
            "voltar com menos trechos que o pedido.",
            EF_SEARCH_SEM_ITERATIVA,
        )
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH_SEM_ITERATIVA}")
    except DatabaseError:
        pass  # sem índice HNSW, o Postgres faz a busca exata


def _trechos_do_sistema(vetor, k, modelo):
    embeddings = (
        EmbeddingVersion.objects.filter(
            vigente=True,
            modelo=modelo,
            chunk__documento__in=Documento.objects.disponiveis(),
        )
        .select_related("chunk__documento")
        .annotate(distancia=CosineDistance("embedding", vetor))
        .order_by("distancia")[:k]
    )
    trechos = []
    for e in embeddings:
        chunk, doc = e.chunk, e.chunk.documento
        trechos.append(
            {
                "documento_id": str(doc.id),
                "documento": doc.identificador_codigo,
                "revisao": doc.numero_revisao or None,
                "titulo": doc.titulo,
                "url_pdf": None,
                "pagina_inicio": chunk.pagina_inicio,
                "pagina_fim": chunk.pagina_fim,
                "texto": chunk.texto,
                "similaridade": 1 - float(e.distancia),
            }
        )
    return trechos


def _trechos_da_base_publica(vetor, k, modelo):
    texto_vetor = _vetor_sql(vetor)
    try:
        # Savepoint: se a tabela não existir, só esta consulta é desfeita.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(SQL_BASE_PUBLICA, [texto_vetor, modelo, texto_vetor, k])
            linhas = cursor.fetchall()
    except ProgrammingError:
        _avisar_uma_vez(
            "sem_base_publica",
            "Tabela %s não encontrada no banco do Django: a busca está usando "
            "só os documentos do sistema. Rode gerar_embeddings/"
            "importar_embeddings com o DATABASE_URL apontando para este banco.",
            TABELA_BASE_PUBLICA,
        )
        return []
    return [
        {
            "documento_id": None,
            "documento": documento,
            "revisao": None,
            "titulo": titulo,
            "url_pdf": url_pdf or None,
            "pagina_inicio": p1,
            "pagina_fim": p2,
            "texto": texto,
            "similaridade": float(similaridade),
        }
        for documento, titulo, url_pdf, p1, p2, texto, similaridade in linhas
    ]


def buscar_por_vetor(vetor, k, modelo):
    """Os k trechos mais parecidos com `vetor` (lista de floats, gerada pelo
    `modelo` de embedding), do mais parecido ao menos, entre documentos
    disponíveis e a base pública. Cada fonte traz os seus melhores candidatos;
    juntar, ordenar e cortar dá o top-k geral."""
    candidatos = k * FATOR_CANDIDATOS
    with transaction.atomic():
        _configurar_indice()
        trechos = _trechos_do_sistema(
            vetor, candidatos, modelo
        ) + _trechos_da_base_publica(vetor, candidatos, modelo)
    trechos.sort(key=lambda t: t["similaridade"], reverse=True)
    return trechos[:k]
