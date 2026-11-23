"""
AKA-75: nova ação DADOS_PESSOAIS_EXPORTADOS no log de auditoria. Só muda a
lista de choices (validação do Django); a coluna continua varchar(30).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0008_log_auditoria"),
    ]

    operations = [
        migrations.AlterField(
            model_name="logauditoria",
            name="acao",
            field=models.CharField(
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
                    ("DADOS_PESSOAIS_EXPORTADOS", "Dados pessoais exportados"),
                ],
                max_length=30,
            ),
        ),
    ]
