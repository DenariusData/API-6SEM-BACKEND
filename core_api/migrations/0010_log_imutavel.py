"""
Log só de inclusão (AKA-80, LGPD).

1. LogAcesso deixa de apagar em cascata: as FKs para documento e perfil
   viram DO_NOTHING sem constraint no banco. O log sobrevive à exclusão do
   documento (rascunho) e do usuário (exclusão física da LGPD), guardando
   só os ids — mesmo modelo do LogAuditoria (0008).

2. O IP sai do log (decisão de 11/11): IP é dado pessoal pela LGPD e o
   RF21 pede auditoria sem dado pessoal. A coluna ip_origem é removida.
   O DROP COLUMN é DDL e não dispara o trigger de linha do passo 3.

3. Trigger BEFORE UPDATE OR DELETE em log_acesso e log_auditoria: qualquer
   tentativa de alterar ou apagar uma linha é recusada pelo próprio banco,
   venha do ORM, do admin, do shell ou de SQL direto.

TRUNCATE não é bloqueado de propósito: o TransactionTestCase do Django
limpa as tabelas com TRUNCATE entre os testes. Em produção, o usuário do
banco da aplicação não deve ser dono das tabelas (sem TRUNCATE).
"""

import django.db.models.deletion
from django.db import migrations, models

TABELAS = ("log_acesso", "log_auditoria")

MENSAGEM = "O log {} é só de inclusão: não pode ser alterado nem apagado (AKA-80)."

POSTGRES_CRIAR = [
    f"""
    CREATE OR REPLACE FUNCTION fn_log_imutavel() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION '{MENSAGEM.format("%")}', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$ LANGUAGE plpgsql;
    """
] + [
    # DROP IF EXISTS antes: quem aplicou a versão antiga desta migration
    # (0009_log_imutavel, antes do rebase) já tem os triggers no banco.
    f"""
    DROP TRIGGER IF EXISTS trg_{tabela}_imutavel ON {tabela};
    CREATE TRIGGER trg_{tabela}_imutavel
        BEFORE UPDATE OR DELETE ON {tabela}
        FOR EACH ROW EXECUTE FUNCTION fn_log_imutavel();
    """
    for tabela in TABELAS
]

POSTGRES_REMOVER = [
    f"DROP TRIGGER IF EXISTS trg_{tabela}_imutavel ON {tabela};" for tabela in TABELAS
] + ["DROP FUNCTION IF EXISTS fn_log_imutavel();"]

# SQLite (dev local opcional): mesmo efeito, um trigger por operação.
SQLITE_CRIAR = [
    f"""
    CREATE TRIGGER trg_{tabela}_imutavel_{op.lower()}
        BEFORE {op} ON {tabela}
        BEGIN SELECT RAISE(ABORT, '{MENSAGEM.format(tabela)}'); END;
    """
    for tabela in TABELAS
    for op in ("UPDATE", "DELETE")
]

SQLITE_REMOVER = [
    f"DROP TRIGGER IF EXISTS trg_{tabela}_imutavel_{op.lower()};"
    for tabela in TABELAS
    for op in ("UPDATE", "DELETE")
]


# Idempotente como no Postgres; no SQLite é um comando por execute.
SQLITE_CRIAR = SQLITE_REMOVER + SQLITE_CRIAR


def _executar(schema_editor, por_banco):
    comandos = por_banco.get(schema_editor.connection.vendor, [])
    for sql in comandos:
        # params=None: o % do RAISE do plpgsql não é placeholder do driver.
        schema_editor.execute(sql, None)


def criar_triggers(apps, schema_editor):
    _executar(
        schema_editor, {"postgresql": POSTGRES_CRIAR, "sqlite": SQLITE_CRIAR}
    )


def remover_triggers(apps, schema_editor):
    _executar(
        schema_editor, {"postgresql": POSTGRES_REMOVER, "sqlite": SQLITE_REMOVER}
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0009_log_auditoria_dados_exportados"),
    ]

    operations = [
        migrations.AlterField(
            model_name="logacesso",
            name="documento",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="logs_acesso",
                to="core_api.documento",
            ),
        ),
        migrations.AlterField(
            model_name="logacesso",
            name="usuario",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="logs_acesso",
                to="core_api.perfiloperacional",
            ),
        ),
        migrations.RemoveField(
            model_name="logacesso",
            name="ip_origem",
        ),
        migrations.RunPython(criar_triggers, remover_triggers),
    ]
