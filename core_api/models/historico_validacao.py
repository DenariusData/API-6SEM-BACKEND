"""
Histórico do fluxo de validação do documento (S2-33).

Uma linha por ação (ENVIADO, DEVOLVIDO, REENVIADO, APROVADO), com quem fez
e quando. É um registro de auditoria: SÓ INSERÇÃO, nunca edita nem apaga.

A imutabilidade tem duas camadas, porque só a do model não protege contra
um `UPDATE` feito direto no banco ou por `QuerySet.update()`:
  1. aqui no model/queryset (erro claro, antes de chegar no banco);
  2. trigger `trg_historico_validacao_imutavel` (migration
     0005_validacao_documento), que recusa UPDATE e DELETE de qualquer origem.
`documento` é PROTECT (não CASCADE) pelo mesmo motivo: apagar um documento
não pode levar o histórico junto.
"""

from django.db import models

from core_api.models.documento import Documento
from core_api.models.perfil_operacional import PerfilOperacional
from core_api.models.taxonomia import NivelSigilo


class HistoricoImutavelError(Exception):
    """Tentativa de editar ou apagar uma linha do histórico de validação."""


class AcaoValidacao(models.TextChoices):
    ENVIADO = "ENVIADO", "Enviado"
    DEVOLVIDO = "DEVOLVIDO", "Devolvido"
    REENVIADO = "REENVIADO", "Reenviado"
    APROVADO = "APROVADO", "Aprovado"


class HistoricoValidacaoQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise HistoricoImutavelError("O histórico de validação não pode ser editado.")

    def delete(self):
        raise HistoricoImutavelError("O histórico de validação não pode ser apagado.")


class HistoricoValidacao(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.PROTECT, related_name="historico_validacao"
    )
    acao = models.CharField(max_length=20, choices=AcaoValidacao.choices)
    usuario = models.ForeignKey(
        PerfilOperacional, on_delete=models.PROTECT, related_name="acoes_validacao"
    )
    comentario = models.TextField(blank=True, default="")
    # Só preenchido quando acao = APROVADO: o nível de sigilo FINAL decidido.
    nivel_sigilo = models.ForeignKey(
        NivelSigilo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="historico_validacao",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoValidacaoQuerySet.as_manager()

    class Meta:
        app_label = "core_api"
        db_table = "historico_validacao"
        ordering = ["criado_em", "id"]
        indexes = [
            models.Index(
                fields=["documento", "criado_em"], name="hist_validacao_doc_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(acao="APROVADO")
                | models.Q(nivel_sigilo__isnull=True),
                name="ck_historico_nivel_so_na_aprovacao",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise HistoricoImutavelError(
                "O histórico de validação não pode ser editado."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise HistoricoImutavelError("O histórico de validação não pode ser apagado.")

    def __str__(self):
        return f"{self.documento_id} - {self.acao} por {self.usuario_id}"
