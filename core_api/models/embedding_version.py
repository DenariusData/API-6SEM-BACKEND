"""Embedding de um chunk, versionado por modelo (bge-m3 = 1024 dims)."""

from django.db import models
from pgvector.django import HnswIndex, VectorField

from core_api.models.chunk import Chunk


class EmbeddingVersion(models.Model):
    DIMENSAO_ATUAL = 1024

    chunk = models.ForeignKey(
        Chunk, on_delete=models.CASCADE, related_name="embeddings"
    )
    modelo = models.CharField(max_length=60)
    dimensao = models.PositiveIntegerField(default=DIMENSAO_ATUAL)
    embedding = VectorField(dimensions=DIMENSAO_ATUAL)
    vigente = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "embedding_version"
        constraints = [
            models.UniqueConstraint(
                fields=["chunk"],
                condition=models.Q(vigente=True),
                name="uq_embedding_version_vigente_por_chunk",
            ),
        ]
        indexes = [
            HnswIndex(
                name="embedding_version_hnsw_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        status = "vigente" if self.vigente else "histórico"
        return f"{self.chunk_id} - {self.modelo} ({status})"
