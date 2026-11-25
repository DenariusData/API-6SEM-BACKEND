"""
Tipos de documento passam a ser os 4 que o classificador sugere no upload:
Técnica, Jurídica, Normativa e Quantitativa.

Os 9 tipos antigos não são apagados (os documentos que já os usam continuam
com eles): ficam com ativo=False e somem do formulário de upload.
"""

from django.db import migrations, models

TIPOS_NOVOS = ["Técnica", "Jurídica", "Normativa", "Quantitativa"]


def usar_os_quatro_tipos(apps, schema_editor):
    TipoDocumento = apps.get_model("core_api", "TipoDocumento")
    for nome in TIPOS_NOVOS:
        TipoDocumento.objects.update_or_create(nome=nome, defaults={"ativo": True})
    TipoDocumento.objects.exclude(nome__in=TIPOS_NOVOS).update(ativo=False)


def reativar_todos(apps, schema_editor):
    TipoDocumento = apps.get_model("core_api", "TipoDocumento")
    TipoDocumento.objects.update(ativo=True)


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0015_seed_termos"),
    ]

    operations = [
        migrations.AddField(
            model_name="tipodocumento",
            name="ativo",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(usar_os_quatro_tipos, reativar_todos),
    ]
