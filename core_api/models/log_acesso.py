"""
Log de acesso a documento.

✅ DECISÃO TOMADA (resolvendo a duplicidade apontada anteriormente):
log_acesso fica relacional, neste banco ("default") - não existe
coleção equivalente no MongoDB. Justificativa da equipe: é dado comum
e estruturado; NoSQL fica reservado pra dado não estruturado/de
formato variável.
"""

from django.db import models

from core_api.models.documento import Documento
from core_api.models.perfil_operacional import PerfilOperacional


class AcaoAcesso(models.TextChoices):
    VISUALIZACAO = "VISUALIZACAO", "Visualização"
    DOWNLOAD = "DOWNLOAD", "Download"
    EXPORTACAO = "EXPORTACAO", "Exportação"


class LogAcesso(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name="logs_acesso"
    )
    usuario = models.ForeignKey(
        PerfilOperacional, on_delete=models.CASCADE, related_name="logs_acesso"
    )
    acao = models.CharField(max_length=20, choices=AcaoAcesso.choices)
    ip_origem = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "log_acesso"
        indexes = [models.Index(fields=["documento", "criado_em"])]

    def __str__(self):
        return f"{self.usuario_id} {self.acao} {self.documento_id}"
