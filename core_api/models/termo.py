"""
Termos e consentimento (AKA-109, LGPD art. 7º, I e art. 8º).

- Termo: texto que o usuário precisa aceitar (obrigatório) ou pode aceitar
  (opcional). Nova versão = novo registro; a anterior fica inativa.
- AceiteTermo: cada resposta do usuário (aceitou ou recusou/revogou), com a
  versão e a data. Nunca é editado: aceitar ou revogar grava uma linha nova,
  e vale a mais recente. Guarda só o id do usuário (banco de negócio).
"""

from django.db import models


class Termo(models.Model):
    titulo = models.CharField(max_length=150)
    texto = models.TextField()
    versao = models.CharField(max_length=20)
    obrigatorio = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True)
    criado_por_id = models.UUIDField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "termo"
        ordering = ["-obrigatorio", "id"]
        verbose_name = "Termo"
        verbose_name_plural = "Termos"

    def __str__(self) -> str:
        return f"{self.titulo} v{self.versao}"


class AceiteTermo(models.Model):
    usuario_id = models.UUIDField()
    termo = models.ForeignKey(Termo, on_delete=models.PROTECT, related_name="aceites")
    versao = models.CharField(max_length=20)
    aceito = models.BooleanField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "aceite_termo"
        ordering = ["-criado_em", "-id"]
        indexes = [
            models.Index(
                fields=["usuario_id", "termo"], name="aceite_termo_usuario_idx"
            )
        ]
        verbose_name = "Aceite de termo"
        verbose_name_plural = "Aceites de termos"

    def save(self, *args, **kwargs):
        # Só inclusão: uma resposta nova é sempre uma linha nova.
        if self.pk is not None:
            raise ValueError(
                "Aceite de termo não pode ser alterado; grave uma nova resposta."
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.usuario_id} - {self.termo_id} v{self.versao}: {self.aceito}"
