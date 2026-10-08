"""
Base do fluxo de validação do documento (S2-33).

Ordem das operações importa (por isso migration escrita à mão):
  1. colunas novas — `nivel_sigilo_sugerido` entra NULLABLE para poder
     existir em banco que já tem documentos;
  2. backfill dos documentos existentes;
  3. `nivel_sigilo_sugerido` passa a NOT NULL (já sem linhas nulas);
  4. HistoricoValidacao + constraints;
  5. triggers por último, com os dados já ajustados.

Backfill (documentos que JÁ existiam):
  - `nivel_sigilo_sugerido` = `nivel_sigilo` (o único nível que eles têm);
  - `status_validacao` = DISPONIVEL, EXCETO documentos CONFIDENCIAIS, que
    ficam AGUARDANDO_VALIDACAO. Motivo: a regra do banco proíbe
    CONFIDENCIAL + DISPONIVEL sem `validado_por`/`validado_em`, e documento
    antigo não tem validador — inventar um seria forjar registro de
    auditoria. Esses documentos precisam passar por uma validação de
    verdade antes de aparecer na busca.
"""

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F

NOME_NIVEL_CONFIDENCIAL = "CONFIDENCIAL"


def backfill_documentos_existentes(apps, schema_editor):
    Documento = apps.get_model("core_api", "Documento")
    db = schema_editor.connection.alias
    docs = Documento.objects.using(db)

    docs.update(nivel_sigilo_sugerido_id=F("nivel_sigilo_id"))
    docs.exclude(nivel_sigilo__nome=NOME_NIVEL_CONFIDENCIAL).update(
        status_validacao="DISPONIVEL"
    )

    # O UPDATE acima dispara os triggers DEFERRABLE INITIALLY DEFERRED de
    # documento (0003: "exatamente uma área principal") e as FKs deferidas,
    # que ficam como "pending trigger events" até o COMMIT. Enquanto houver
    # evento pendente o Postgres recusa qualquer ALTER TABLE na tabela
    # ("cannot ALTER TABLE ... because it has pending trigger events") — e o
    # AlterField/AddConstraint logo abaixo estão na mesma transação. Em banco
    # vazio isso não aparece; só com documentos. Forçar IMMEDIATE executa os
    # eventos agora (revalidando de quebra a área principal dos documentos
    # existentes) e libera os ALTERs.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    schema_editor.execute("SET CONSTRAINTS ALL DEFERRED")


SQL_UP = """
-- Regra: documento CONFIDENCIAL só pode ficar DISPONIVEL com validado_por e
-- validado_em preenchidos. Não dá pra ser CHECK (precisa ler nivel_sigilo.nome
-- em outra tabela), então é trigger — mesmo padrão da 0003.
CREATE OR REPLACE FUNCTION fn_check_documento_confidencial_validado()
RETURNS TRIGGER AS $$
DECLARE
    v_nivel TEXT;
BEGIN
    IF NEW.status_validacao = 'DISPONIVEL'
       AND (NEW.validado_por_id IS NULL OR NEW.validado_em IS NULL) THEN
        SELECT nome INTO v_nivel FROM nivel_sigilo WHERE id = NEW.nivel_sigilo_id;
        IF v_nivel = 'CONFIDENCIAL' THEN
            RAISE EXCEPTION 'Documento % é CONFIDENCIAL e só pode ficar DISPONIVEL com validado_por e validado_em preenchidos', NEW.id
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'ck_documento_confidencial_validado';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_documento_confidencial_validado
BEFORE INSERT OR UPDATE ON documento
FOR EACH ROW EXECUTE FUNCTION fn_check_documento_confidencial_validado();

-- Histórico de validação é só inserção: recusa UPDATE e DELETE de qualquer
-- origem (ORM, psql, outro serviço). TRUNCATE não passa por trigger de linha,
-- o que mantém o flush dos testes funcionando.
CREATE OR REPLACE FUNCTION fn_historico_validacao_imutavel()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'historico_validacao é somente inserção (% bloqueado)', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_historico_validacao_imutavel
BEFORE UPDATE OR DELETE ON historico_validacao
FOR EACH ROW EXECUTE FUNCTION fn_historico_validacao_imutavel();
"""

SQL_DOWN = """
DROP TRIGGER IF EXISTS trg_historico_validacao_imutavel ON historico_validacao;
DROP FUNCTION IF EXISTS fn_historico_validacao_imutavel();
DROP TRIGGER IF EXISTS trg_documento_confidencial_validado ON documento;
DROP FUNCTION IF EXISTS fn_check_documento_confidencial_validado();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0004_registro_pergunta"),
    ]

    operations = [
        # 1. colunas novas
        migrations.AddField(
            model_name="documento",
            name="status_validacao",
            field=models.CharField(
                choices=[
                    ("AGUARDANDO_VALIDACAO", "Aguardando validação"),
                    ("DEVOLVIDO", "Devolvido"),
                    ("DISPONIVEL", "Disponível"),
                ],
                db_index=True,
                default="AGUARDANDO_VALIDACAO",
                max_length=25,
            ),
        ),
        migrations.AddField(
            model_name="documento",
            name="nivel_sigilo_sugerido",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos_sugeridos",
                to="core_api.nivelsigilo",
            ),
        ),
        migrations.AddField(
            model_name="documento",
            name="validado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos_validados",
                to="core_api.perfiloperacional",
            ),
        ),
        migrations.AddField(
            model_name="documento",
            name="validado_em",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documento",
            name="comentario_devolucao",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documento",
            name="status_processamento",
            field=models.CharField(
                choices=[
                    ("PENDENTE", "Pendente"),
                    ("PROCESSANDO", "Processando"),
                    ("CONCLUIDO", "Concluído"),
                    ("ERRO", "Erro"),
                ],
                default="PENDENTE",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="documento",
            name="erro_processamento",
            field=models.TextField(blank=True, null=True),
        ),
        # 2. backfill
        migrations.RunPython(backfill_documentos_existentes, migrations.RunPython.noop),
        # 3. sugerido passa a obrigatório
        migrations.AlterField(
            model_name="documento",
            name="nivel_sigilo_sugerido",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="documentos_sugeridos",
                to="core_api.nivelsigilo",
            ),
        ),
        migrations.AddConstraint(
            model_name="documento",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("validado_em__isnull", True), ("validado_por__isnull", True)
                    ),
                    models.Q(
                        ("validado_em__isnull", False), ("validado_por__isnull", False)
                    ),
                    _connector="OR",
                ),
                name="ck_documento_validacao_completa",
            ),
        ),
        # 4. histórico
        migrations.CreateModel(
            name="HistoricoValidacao",
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
                (
                    "acao",
                    models.CharField(
                        choices=[
                            ("ENVIADO", "Enviado"),
                            ("DEVOLVIDO", "Devolvido"),
                            ("REENVIADO", "Reenviado"),
                            ("APROVADO", "Aprovado"),
                        ],
                        max_length=20,
                    ),
                ),
                ("comentario", models.TextField(blank=True, default="")),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "documento",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="historico_validacao",
                        to="core_api.documento",
                    ),
                ),
                (
                    "nivel_sigilo",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="historico_validacao",
                        to="core_api.nivelsigilo",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="acoes_validacao",
                        to="core_api.perfiloperacional",
                    ),
                ),
            ],
            options={
                "db_table": "historico_validacao",
                "ordering": ["criado_em", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="historicovalidacao",
            index=models.Index(
                fields=["documento", "criado_em"], name="hist_validacao_doc_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="historicovalidacao",
            constraint=models.CheckConstraint(
                condition=models.Q(("acao", "APROVADO"))
                | models.Q(("nivel_sigilo__isnull", True)),
                name="ck_historico_nivel_so_na_aprovacao",
            ),
        ),
        # 5. triggers
        migrations.RunSQL(SQL_UP, reverse_sql=SQL_DOWN),
    ]
