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
from core_api.models.log_imutavel import LogImutavelMixin
from core_api.models.perfil_operacional import PerfilOperacional


class AcaoAcesso(models.TextChoices):
    VISUALIZACAO = "VISUALIZACAO", "Visualização"
    DOWNLOAD = "DOWNLOAD", "Download"
    EXPORTACAO = "EXPORTACAO", "Exportação"


class LogAcesso(LogImutavelMixin):
    # Só de inclusão (AKA-80): o log sobrevive ao documento e ao perfil.
    # Sem constraint no banco e DO_NOTHING, apagar um rascunho ou excluir um
    # usuário (LGPD) não tenta apagar o log em cascata — o que o trigger de
    # imutabilidade recusaria. A linha guarda só os ids, nunca dado pessoal.
    documento = models.ForeignKey(
        Documento,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="logs_acesso",
    )
    usuario = models.ForeignKey(
        PerfilOperacional,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="logs_acesso",
    )
    acao = models.CharField(max_length=20, choices=AcaoAcesso.choices)
    # Sem IP (AKA-80): IP é dado pessoal pela LGPD e o RF21 pede auditoria
    # sem dado pessoal. O log guarda só usuário (uuid), documento, ação e data.
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "log_acesso"
        indexes = [models.Index(fields=["documento", "criado_em"])]

    def __str__(self):
        return f"{self.usuario_id} {self.acao} {self.documento_id}"
