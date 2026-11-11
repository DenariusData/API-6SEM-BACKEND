"""
Log só de inclusão (AKA-80, épico E5 — Privacidade e LGPD).

`LogAcesso` e `LogAuditoria` são a trilha de auditoria do sistema: uma linha
gravada não pode ser alterada nem apagada, por ninguém. A proteção fica em
duas camadas:

  1. Banco (a que vale): trigger BEFORE UPDATE OR DELETE nas tabelas
     log_acesso e log_auditoria (migration 0009) recusa a operação, venha
     de onde vier — ORM, SQL direto, admin, shell.
  2. Aplicação (para o erro aparecer cedo e claro): este mixin faz save()
     de linha existente, delete(), QuerySet.update() e QuerySet.delete()
     levantarem LogImutavelError antes de chegar no banco.

Só INSERT é permitido. Correção de um registro errado é uma linha nova,
nunca a edição da antiga.
"""

from django.db import models


class LogImutavelError(Exception):
    """Tentativa de alterar ou apagar uma linha do log."""

    def __init__(self, tabela):
        super().__init__(
            f"O log '{tabela}' é só de inclusão: registros não podem ser "
            "alterados nem apagados (AKA-80, LGPD)."
        )


class LogImutavelQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise LogImutavelError(self.model._meta.db_table)

    def delete(self):
        raise LogImutavelError(self.model._meta.db_table)

    def bulk_update(self, objs, fields, batch_size=None):
        raise LogImutavelError(self.model._meta.db_table)

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        raise LogImutavelError(self.model._meta.db_table)


class LogImutavelMixin(models.Model):
    objects = LogImutavelQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding or kwargs.get("force_update"):
            raise LogImutavelError(self._meta.db_table)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LogImutavelError(self._meta.db_table)
