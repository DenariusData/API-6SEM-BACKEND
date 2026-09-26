"""
Registro de cada pergunta feita na tela de pergunta (banco de NEGÓCIO).

Serve para acompanhar o desempenho do RAG: quanto tempo levou a busca dos
trechos, quanto levou a IA e se a pergunta tinha base no acervo. Guarda só o
`usuario_id` (uuid), sem FK para `credenciais.Usuario`, que fica em outro banco.
"""

from django.db import models


class RegistroPergunta(models.Model):
    usuario_id = models.UUIDField(null=True, blank=True)
    pergunta = models.TextField()
    encontrou = models.BooleanField()
    quantidade_fontes = models.PositiveSmallIntegerField(default=0)
    tempo_busca_ms = models.PositiveIntegerField()
    tempo_ia_ms = models.PositiveIntegerField()
    tempo_total_ms = models.PositiveIntegerField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "registro_pergunta"
        ordering = ["-criado_em"]
        verbose_name = "Registro de Pergunta"
        verbose_name_plural = "Registros de Perguntas"

    def __str__(self) -> str:
        return f"{self.pergunta[:50]} ({self.tempo_total_ms} ms)"
