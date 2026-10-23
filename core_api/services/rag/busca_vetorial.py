"""
Busca vetorial dos trechos que a IA pode usar (AKA-51 / S2-37).

Duas fontes, juntadas e ordenadas pela similaridade com a pergunta:

    1. Documentos do sistema (upload -> validação -> processamento): tabelas
       `chunk` + `embedding_version`. Só entram documentos de
       `Documento.objects.disponiveis()`. Esse é o ÚNICO lugar do filtro: a
       regra de permissão da Sprint 3 vai ser somada ali, e a busca passa a
       respeitá-la sem mudar nada aqui.

    2. Base pública do EverySpec: tabela `rag_chunks`, gerada pelos comandos
       gerar_embeddings/importar_embeddings. São normas públicas, que não
       passam pelo fluxo de validação, então não têm Documento nem filtro.
       Se a tabela não existir (instalação nova, testes), é ignorada.

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

Documento aguardando validação ou devolvido nunca tem trecho processado (o
processamento só começa na aprovação), mas o filtro não depende disso: mesmo
que exista trecho de um documento não disponível, ele não entra.
"""

from django.db import connection
from pgvector.django import CosineDistance

from core_api.models import Documento, EmbeddingVersion

TABELA_BASE_PUBLICA = "rag_chunks"

SQL_BASE_PUBLICA = f"""
    SELECT documento, titulo, url_pdf, pagina_inicio, pagina_fim, texto,
           1 - (embedding <=> %s::vector) AS similaridade
    FROM {TABELA_BASE_PUBLICA}
    ORDER BY embedding <=> %s::vector
    LIMIT %s
"""


def _vetor_sql(vetor) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vetor) + "]"


def _trechos_do_sistema(vetor, k):
    embeddings = (
        EmbeddingVersion.objects.filter(
            vigente=True,
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


def _tem_base_publica() -> bool:
    return TABELA_BASE_PUBLICA in connection.introspection.table_names()


def _trechos_da_base_publica(vetor, k):
    if not _tem_base_publica():
        return []
    texto_vetor = _vetor_sql(vetor)
    with connection.cursor() as cursor:
        cursor.execute(SQL_BASE_PUBLICA, [texto_vetor, texto_vetor, k])
        linhas = cursor.fetchall()
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


def buscar_por_vetor(vetor, k):
    """Os k trechos mais parecidos com `vetor` (lista de floats), do mais
    parecido ao menos, entre documentos disponíveis e a base pública."""
    trechos = _trechos_do_sistema(vetor, k) + _trechos_da_base_publica(vetor, k)
    trechos.sort(key=lambda t: t["similaridade"], reverse=True)
    return trechos[:k]
