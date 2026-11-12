"""
AKA-24: origem de cada resposta da IA.

- `chunk.secao`: seção do documento onde o trecho começa. Os trechos que já
  existem são preenchidos aqui, documento por documento, com a mesma regra do
  processamento (core_api/services/rag/secoes.py).
- `registro_pergunta`: resposta da IA e modelos usados.
- `registro_pergunta_fonte`: cada trecho que embasou a resposta, com cópia
  literal do texto.
"""

import django.db.models.deletion
from django.db import migrations, models

from core_api.services.rag.secoes import secoes_dos_trechos


def preencher_secoes(apps, schema_editor):
    Chunk = apps.get_model("core_api", "Chunk")
    documentos = Chunk.objects.values_list("documento_id", flat=True).distinct()
    for documento_id in documentos:
        chunks = list(Chunk.objects.filter(documento_id=documento_id).order_by("ordem"))
        for chunk, secao in zip(chunks, secoes_dos_trechos([c.texto for c in chunks])):
            chunk.secao = secao
        Chunk.objects.bulk_update(chunks, ["secao"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0009_log_auditoria_dados_exportados"),
    ]

    operations = [
        migrations.AddField(
            model_name="chunk",
            name="secao",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="registropergunta",
            name="modelo_embedding",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="registropergunta",
            name="modelo_llm",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="registropergunta",
            name="resposta",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="FontePergunta",
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
                ("numero", models.PositiveSmallIntegerField()),
                ("citada", models.BooleanField(default=False)),
                ("documento_id", models.UUIDField(blank=True, null=True)),
                ("trecho_id", models.CharField(blank=True, max_length=100, null=True)),
                ("documento", models.CharField(max_length=300)),
                ("revisao", models.CharField(blank=True, max_length=100, null=True)),
                ("titulo", models.TextField(blank=True, default="")),
                ("pagina_inicio", models.PositiveIntegerField(blank=True, null=True)),
                ("pagina_fim", models.PositiveIntegerField(blank=True, null=True)),
                ("secao", models.CharField(blank=True, max_length=200, null=True)),
                ("similaridade", models.FloatField()),
                ("texto", models.TextField()),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fontes",
                        to="core_api.registropergunta",
                    ),
                ),
            ],
            options={
                "verbose_name": "Fonte da Pergunta",
                "verbose_name_plural": "Fontes das Perguntas",
                "db_table": "registro_pergunta_fonte",
                "ordering": ["registro_id", "numero"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("registro", "numero"), name="uq_registro_pergunta_fonte"
                    )
                ],
            },
        ),
        migrations.RunPython(preencher_secoes, migrations.RunPython.noop),
    ]
