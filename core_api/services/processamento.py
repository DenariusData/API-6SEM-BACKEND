"""
Processamento do documento para a IA (AKA-62 / S2-38).

    processar_documento(documento_id)
        extrai o texto com página -> divide em trechos -> filtra por
        qualidade -> gera o embedding com o bge-m3 local -> grava Chunk e
        EmbeddingVersion ligados ao documento.

    agendar_processamento(documento_id)
        chamado na aprovação (services/validacao.py). Roda o processamento
        numa thread, DEPOIS do commit da aprovação, para o endpoint de aprovar
        responder na hora.

Reaproveita os scripts do pipeline RAG em management/commands:
extrair_texto.processar, gerar_chunks.dividir/qualidade e
gerar_embeddings.gerar_vetores, com os mesmos parâmetros padrão deles.

Estado no próprio documento (campos da AKA-46):
    PENDENTE -> PROCESSANDO -> CONCLUIDO
                            -> ERRO (mensagem em `erro_processamento`)
Os trechos antigos só são trocados pelos novos quando tudo deu certo, numa
transação: um reprocessamento que falha não apaga o que já existia. Para
reprocessar: `python manage.py reprocessar_documento <id>`.

Nenhuma chamada externa (D1): o Ollama precisa estar na própria máquina
(mesma checagem do teste do bge-m3, AKA-95), e o arquivo é lido da pasta
privada do upload, nunca baixado de uma URL.

Limite conhecido: a thread vive no processo do servidor. Se o servidor
parar no meio, o documento fica PROCESSANDO; o comando de reprocessar
resolve. Uma fila de verdade (Celery/RQ) pode substituir a thread sem
mudar processar_documento.
"""

import logging
import threading
from functools import partial
from pathlib import Path

from django.conf import settings
from django.db import connections, transaction
from wordfreq import available_languages

from core_api.management.commands import extrair_texto, gerar_chunks, gerar_embeddings
from core_api.management.commands.testar_bge_m3_local import validar_ollama_local
from core_api.models import Chunk, Documento, EmbeddingVersion, StatusProcessamento

# O mesmo modelo que converte a pergunta em vetor na tela de pergunta: trecho
# e pergunta precisam estar no mesmo espaço de vetores.
from core_api.services.rag.pergunta import MODELO_EMBEDDING

logger = logging.getLogger(__name__)

# Mesmos padrões dos scripts (argparse de gerar_chunks/gerar_embeddings).
TAMANHO_TRECHO = 1500
SOBREPOSICAO = 200
QUALIDADE_MINIMA = 0.8
TAMANHO_LOTE = 16
TAMANHO_MAXIMO_ERRO = 1000


class ErroProcessamento(Exception):
    """Falha esperada no processamento; a mensagem vai para o documento."""


# -- etapas ---------------------------------------------------------------
def _caminho_do_arquivo(documento) -> Path:
    """PDF do documento na pasta privada do upload. Só o nome do arquivo é
    usado (nada de ../ vindo do banco) e URL nunca é baixada (D1)."""
    # DOCUMENTOS_UPLOAD_DIR chega com a AKA-59; o padrão é o mesmo de lá.
    pasta = Path(
        getattr(
            settings,
            "DOCUMENTOS_UPLOAD_DIR",
            Path(settings.BASE_DIR) / "uploads" / "documentos",
        )
    )
    referencia = documento.arquivo_original_url or ""
    if "://" in referencia:
        raise ErroProcessamento(
            "O arquivo do documento é uma URL; só arquivos da pasta privada do "
            "upload são processados (sem chamada externa)."
        )
    caminho = pasta / Path(referencia).name
    if not referencia or not caminho.is_file():
        raise ErroProcessamento("Arquivo do documento não encontrado no servidor.")
    return caminho


def _idioma_do_filtro(documento):
    """Idioma do filtro de qualidade, pelo idioma do documento ("pt-BR" ->
    "pt"). Idioma sem lista de palavras no wordfreq fica sem filtro."""
    codigo = (documento.idioma_id or "en").split("-")[0].lower()
    return codigo if codigo in available_languages() else None


def _trechos(documento, caminho):
    """[(texto, pagina_inicio, pagina_fim, qualidade)] aprovados no filtro."""
    try:
        extraido = extrair_texto.processar(caminho, caminho.name, {})
    except Exception as e:  # noqa: BLE001 - PDF corrompido, protegido etc.
        raise ErroProcessamento(f"Não foi possível ler o PDF: {e}") from e

    idioma = _idioma_do_filtro(documento)
    aprovados = []
    for pedaco in gerar_chunks.dividir(extraido, TAMANHO_TRECHO, SOBREPOSICAO):
        nota = gerar_chunks.qualidade(pedaco["texto"], idioma) if idioma else None
        if nota is not None and nota < QUALIDADE_MINIMA:
            continue
        aprovados.append(
            (
                pedaco["texto"],
                pedaco["pagina_inicio"],
                pedaco["pagina_fim"],
                None if nota is None else round(nota, 3),
            )
        )
    if not aprovados:
        raise ErroProcessamento(
            "Nenhum trecho aproveitável: o PDF não tem texto legível "
            "(digitalizado sem OCR) ou o texto não passou no filtro de qualidade."
        )
    return aprovados


def _embeddings(textos):
    try:
        validar_ollama_local(gerar_embeddings.OLLAMA_URL)
    except ValueError as e:
        raise ErroProcessamento(str(e)) from e

    modelo = MODELO_EMBEDDING
    vetores = []
    try:
        for inicio in range(0, len(textos), TAMANHO_LOTE):
            lote = textos[inicio : inicio + TAMANHO_LOTE]
            vetores += gerar_embeddings.gerar_vetores(lote, modelo)
    except gerar_embeddings.ErroOllama as e:
        raise ErroProcessamento(f"Falha ao gerar os embeddings: {e}") from e

    dimensao = EmbeddingVersion.DIMENSAO_ATUAL
    if any(len(v) != dimensao for v in vetores):
        raise ErroProcessamento(
            f"O modelo {modelo} devolveu vetores fora das {dimensao} dimensões "
            "esperadas."
        )
    return modelo, vetores


def _marcar(documento, status, erro=None):
    documento.status_processamento = status
    documento.erro_processamento = erro
    documento.save(
        update_fields=["status_processamento", "erro_processamento", "atualizado_em"]
    )


# -- entrada --------------------------------------------------------------
def processar_documento(documento_id) -> Documento:
    """Processa um documento e devolve-o com o status final (CONCLUIDO ou
    ERRO). Nunca deixa o documento em PROCESSANDO. Levanta
    Documento.DoesNotExist se o id não existe."""
    documento = Documento.objects.get(pk=documento_id)
    _marcar(documento, StatusProcessamento.PROCESSANDO)
    try:
        trechos = _trechos(documento, _caminho_do_arquivo(documento))
        modelo, vetores = _embeddings([texto for texto, *_ in trechos])
        with transaction.atomic():
            Chunk.objects.filter(documento=documento).delete()
            chunks = Chunk.objects.bulk_create(
                Chunk(
                    documento=documento,
                    ordem=ordem,
                    texto=texto,
                    pagina_inicio=pagina_inicio,
                    pagina_fim=pagina_fim,
                    qualidade=nota,
                )
                for ordem, (texto, pagina_inicio, pagina_fim, nota) in enumerate(
                    trechos
                )
            )
            EmbeddingVersion.objects.bulk_create(
                EmbeddingVersion(chunk=chunk, modelo=modelo, embedding=vetor)
                for chunk, vetor in zip(chunks, vetores)
            )
            _marcar(documento, StatusProcessamento.CONCLUIDO)
    except Exception as e:
        if not isinstance(e, ErroProcessamento):
            logger.exception(
                "Erro inesperado ao processar o documento %s", documento_id
            )
        _marcar(documento, StatusProcessamento.ERRO, str(e)[:TAMANHO_MAXIMO_ERRO])
    return documento


def _processar_em_segundo_plano(documento_id):
    try:
        processar_documento(documento_id)
    except Exception:
        logger.exception("Falha ao processar o documento %s", documento_id)
    finally:
        # A thread abre a própria conexão com o banco; fecha ao terminar.
        connections.close_all()


def _iniciar_thread(documento_id):
    thread = threading.Thread(
        target=_processar_em_segundo_plano,
        args=(documento_id,),
        name=f"processar-documento-{documento_id}",
        daemon=True,
    )
    thread.start()
    return thread


def agendar_processamento(documento_id):
    """Processa o documento em segundo plano depois do commit da transação
    atual (se a aprovação for desfeita, nada roda). Desligável com
    PROCESSAMENTO_AUTOMATICO=false."""
    if settings.PROCESSAMENTO_AUTOMATICO:
        transaction.on_commit(partial(_iniciar_thread, documento_id))
