from django.db import migrations

PAPEIS_INICIAIS = [
    ("ENGENHARIA", "Engenharia"),
    ("QUALIDADE", "Qualidade"),
    ("ADMINISTRADOR", "Administrador"),
]


def criar_papeis(apps, schema_editor):
    Papel = apps.get_model("credenciais", "Papel")
    db_alias = schema_editor.connection.alias
    for codigo, descricao in PAPEIS_INICIAIS:
        Papel.objects.using(db_alias).get_or_create(
            codigo=codigo, defaults={"descricao": descricao}
        )


def remover_papeis(apps, schema_editor):
    Papel = apps.get_model("credenciais", "Papel")
    db_alias = schema_editor.connection.alias
    Papel.objects.using(db_alias).filter(
        codigo__in=[c for c, _ in PAPEIS_INICIAIS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("credenciais", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(criar_papeis, remover_papeis),
    ]
