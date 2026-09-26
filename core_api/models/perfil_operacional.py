"""
Perfil operacional do usuário (banco de NEGÓCIO).

Guarda apenas dados funcionais/operacionais da aplicação — NUNCA dados
pessoais sensíveis (esses ficam em `credenciais.models.Usuario`, em banco
separado). A ligação entre os dois bancos é feita só pelo valor de
`usuario_id` (o mesmo uuid do `credenciais.Usuario.id`); não existe FK física
entre bancos diferentes, então a composição é feita na camada de serviço
(`core_api.services.auth.autenticacao`).
"""

from django.db import models


class PerfilOperacional(models.Model):
    usuario_id = models.UUIDField(primary_key=True, editable=False)
    matricula = models.CharField(max_length=20, unique=True)
    cargo = models.CharField(max_length=150, blank=True, default="")
    avatar = models.CharField(max_length=255, default="/avatars/default.png")
    allowed_menus = models.JSONField(default=list, blank=True)

    class Meta:
        app_label = "core_api"
        db_table = "perfil_operacional"
        verbose_name = "Perfil Operacional"
        verbose_name_plural = "Perfis Operacionais"

    def __str__(self) -> str:
        return f"Perfil operacional de {self.usuario_id}"
