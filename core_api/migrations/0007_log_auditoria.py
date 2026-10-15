"""
Log de auditoria (AKA-31): tabela nova, sem FK para nenhuma outra (o usuário
é guardado só pelo uuid do PerfilOperacional).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0006_registropergunta_idioma"),
    ]

    operations = [
        migrations.CreateModel(
            name="LogAuditoria",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("usuario_id", models.UUIDField(blank=True, null=True)),
                (
                    "acao",
                    models.CharField(
                        choices=[
                            ("CRIADO", "Criado"),
                            ("ATUALIZADO", "Atualizado"),
                            ("EXCLUIDO", "Excluído"),
                            ("ENVIADO", "Enviado"),
                            ("DEVOLVIDO", "Devolvido"),
                            ("REENVIADO", "Reenviado"),
                            ("APROVADO", "Aprovado"),
                            ("LOGIN", "Login"),
                            ("LOGIN_FALHOU", "Login falhou"),
                            ("DADOS_PESSOAIS_ALTERADOS", "Dados pessoais alterados"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "entidade",
                    models.CharField(
                        choices=[
                            ("DOCUMENTO", "Documento"),
                            ("DOCUMENTO_AREA", "Área do documento"),
                            ("HISTORICO_VALIDACAO", "Histórico de validação"),
                            ("USUARIO", "Usuário"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "registro_id",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "campos_alterados",
                    models.JSONField(blank=True, default=list),
                ),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "log_auditoria",
                "ordering": ["criado_em", "id"],
                "indexes": [
                    models.Index(
                        fields=["entidade", "registro_id"],
                        name="log_auditoria_registro_idx",
                    ),
                    models.Index(
                        fields=["usuario_id", "criado_em"],
                        name="log_auditoria_usuario_idx",
                    ),
                ],
            },
        ),
    ]
