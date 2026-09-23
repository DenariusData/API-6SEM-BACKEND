"""Chunk de um documento, usado pelo pipeline RAG (gerar_chunks.py)."""

from django.db import models

from core_api.models.documento import Documento


class Chunk(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name="chunks"
    )
    ordem = models.PositiveIntegerField(
        help_text="Posição do chunk dentro do documento."
    )
    pagina_inicio = models.PositiveIntegerField(null=True, blank=True)
    pagina_fim = models.PositiveIntegerField(null=True, blank=True)
    qualidade = models.FloatField(null=True, blank=True)
    texto = models.TextField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "chunk"
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "ordem"], name="uq_chunk_documento_ordem"
            ),
        ]
        ordering = ["documento_id", "ordem"]

    def __str__(self):
        return f"{self.documento_id} #{self.ordem}"
