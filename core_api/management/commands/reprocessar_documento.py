"""
Reprocessa para a IA um documento (AKA-62): extrai o texto, gera os trechos e
os embeddings de novo e substitui os anteriores. Roda na hora, sem thread.

Uso:
    python manage.py reprocessar_documento <id do documento>

Serve para o documento que ficou com status de processamento ERRO (o motivo
está em `erro_processamento`) ou que ficou PROCESSANDO porque o servidor parou
no meio. Pré-requisito: Ollama local com o bge-m3 (`ollama pull bge-m3`).
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from core_api.models import Documento, StatusProcessamento
from core_api.services.processamento import processar_documento


class Command(BaseCommand):
    help = "Reprocessa para a IA (trechos + embeddings) o documento informado."

    def add_arguments(self, parser):
        parser.add_argument("documento_id", help="id (uuid) do documento")

    def handle(self, *args, **opts):
        try:
            documento_id = uuid.UUID(opts["documento_id"])
        except ValueError:
            raise CommandError("Informe o id (uuid) do documento.")

        self.stdout.write(f"Processando o documento {documento_id}...")
        try:
            documento = processar_documento(documento_id)
        except Documento.DoesNotExist:
            raise CommandError(f"Documento {documento_id} não encontrado.")

        if documento.status_processamento != StatusProcessamento.CONCLUIDO:
            raise CommandError(
                f"Falha no processamento: {documento.erro_processamento}"
            )
        trechos = documento.chunks.count()
        self.stdout.write(
            self.style.SUCCESS(f"Concluído: {trechos} trechos com embedding.")
        )
