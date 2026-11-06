"""
Campo 14 do upload (S2-32): "Norma ou documento relacionado", em texto livre
nesta sprint. Coluna nula, então não precisa de backfill. As relações entre
documentos (`documento_relacao`) ficam para a Sprint 3.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0006_registropergunta_idioma"),
    ]

    operations = [
        migrations.AddField(
            model_name="documento",
            name="referencia_normativa",
            field=models.TextField(blank=True, null=True),
        ),
    ]
