"""
Testes do log só de inclusão (AKA-80). Um bloco por critério:

  1. A aplicação não altera nem apaga linha do log (save, delete, update).
  2. O banco recusa UPDATE/DELETE mesmo com SQL direto, fora do ORM.
  3. Apagar documento ou usuário não leva o log junto.
  4. O log não guarda dado pessoal: nem na estrutura das tabelas, nem nos
     valores gravados depois de um fluxo real (login, login errado, troca
     de nome, upload, aprovação e consulta de documento).

Usa a mesma base do test_log_auditoria (TransactionTestCase, dois bancos),
porque os triggers precisam do commit de verdade e o nome/e-mail vivem em
credenciais_db.
"""

import json
import uuid

from django.db import DatabaseError, connection, transaction

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    Documento,
    EntidadeAuditoria,
    LogAcesso,
    LogAuditoria,
    LogImutavelError,
    PerfilOperacional,
)
from core_api.services.auditoria import registrar_acao
from credenciais.models import Usuario
from tests.integration.test_log_auditoria import AuditoriaBase

NOME = "Josefina Albuquerque"
SENHA = "x12345"
TELEFONE = "12 98888-7777"


class LogImutavelBase(AuditoriaBase):
    def log_auditoria(self):
        return registrar_acao(
            self.autor["id"], AcaoAuditoria.LOGIN, EntidadeAuditoria.USUARIO
        )

    def log_acesso(self):
        perfil = PerfilOperacional.objects.get(pk=self.autor["id"])
        with transaction.atomic():
            doc, _ = self.novo_documento(perfil, f"DOC-{uuid.uuid4().hex[:6]}")
        return LogAcesso.objects.create(
            documento=doc,
            usuario=perfil,
            acao=AcaoAcesso.VISUALIZACAO,
            ip_origem="10.0.0.1",
        )

    def sql(self, comando, params):
        """Executa SQL direto, sem passar pelo ORM nem pelo mixin."""
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(comando, params)


class AplicacaoNaoAlteraOLogTests(LogImutavelBase):
    def test_save_de_linha_existente_e_recusado(self):
        for log, campo, valor in (
            (self.log_auditoria(), "acao", AcaoAuditoria.LOGIN_FALHOU),
            (self.log_acesso(), "acao", AcaoAcesso.DOWNLOAD),
        ):
            with self.subTest(tabela=log._meta.db_table):
                original = getattr(log, campo)
                setattr(log, campo, valor)
                with self.assertRaises(LogImutavelError):
                    log.save()
                log.refresh_from_db()
                self.assertEqual(getattr(log, campo), original)

    def test_delete_da_linha_e_recusado(self):
        for log in (self.log_auditoria(), self.log_acesso()):
            with self.subTest(tabela=log._meta.db_table):
                with self.assertRaises(LogImutavelError):
                    log.delete()
                self.assertTrue(type(log).objects.filter(pk=log.pk).exists())

    def test_update_e_delete_em_massa_sao_recusados(self):
        self.log_auditoria()
        self.log_acesso()
        for modelo in (LogAuditoria, LogAcesso):
            with self.subTest(tabela=modelo._meta.db_table):
                antes = list(modelo.objects.order_by("id").values())
                self.assertTrue(antes)
                with self.assertRaises(LogImutavelError):
                    modelo.objects.all().update(acao="X")
                with self.assertRaises(LogImutavelError):
                    modelo.objects.all().delete()
                self.assertEqual(list(modelo.objects.order_by("id").values()), antes)

    def test_inclusao_continua_funcionando(self):
        antes = LogAuditoria.objects.count()
        self.log_auditoria()
        self.log_auditoria()
        self.assertEqual(LogAuditoria.objects.count(), antes + 2)


class BancoRecusaAlteracaoTests(LogImutavelBase):
    def test_update_por_sql_direto_e_recusado_pelo_banco(self):
        log = self.log_auditoria()
        with self.assertRaisesMessage(DatabaseError, "só de inclusão"):
            self.sql(
                "UPDATE log_auditoria SET acao = %s WHERE id = %s",
                ["LOGIN_FALHOU", log.pk],
            )
        log.refresh_from_db()
        self.assertEqual(log.acao, AcaoAuditoria.LOGIN)

    def test_delete_por_sql_direto_e_recusado_pelo_banco(self):
        for log in (self.log_auditoria(), self.log_acesso()):
            tabela = log._meta.db_table
            with self.subTest(tabela=tabela):
                with self.assertRaisesMessage(DatabaseError, "só de inclusão"):
                    self.sql(f"DELETE FROM {tabela} WHERE id = %s", [log.pk])
                self.assertTrue(type(log).objects.filter(pk=log.pk).exists())

    def test_queryset_puro_do_django_tambem_e_barrado_no_banco(self):
        # Mesmo que alguém contorne o mixin com um QuerySet comum, o
        # trigger do banco segura.
        from django.db.models import QuerySet

        log = self.log_auditoria()
        with self.assertRaises(DatabaseError), transaction.atomic():
            QuerySet(model=LogAuditoria).filter(pk=log.pk).delete()
        self.assertTrue(LogAuditoria.objects.filter(pk=log.pk).exists())


class LogSobreviveAExclusaoTests(LogImutavelBase):
    def test_apagar_documento_nao_apaga_o_log_de_acesso(self):
        acesso = self.log_acesso()
        doc_id = acesso.documento_id
        Documento.objects.get(pk=doc_id).delete()

        self.assertFalse(Documento.objects.filter(pk=doc_id).exists())
        sobrou = LogAcesso.objects.get(pk=acesso.pk)
        self.assertEqual(sobrou.documento_id, doc_id)

    def test_excluir_perfil_do_usuario_nao_apaga_o_log(self):
        # Exclusão física do usuário (LGPD): o log fica, só com o uuid.
        usuario, _ = self.usuario("ENGENHARIA", "Usuário que será excluído")
        perfil = PerfilOperacional.objects.get(pk=usuario["id"])
        autor = PerfilOperacional.objects.get(pk=self.autor["id"])
        with transaction.atomic():
            doc, _ = self.novo_documento(autor, "DOC-EXCLUSAO")
        acesso = LogAcesso.objects.create(
            documento=doc, usuario=perfil, acao=AcaoAcesso.VISUALIZACAO
        )
        auditoria = registrar_acao(
            perfil.pk, AcaoAuditoria.LOGIN, EntidadeAuditoria.USUARIO
        )

        perfil.delete()

        self.assertEqual(
            str(LogAcesso.objects.get(pk=acesso.pk).usuario_id), usuario["id"]
        )
        self.assertEqual(
            str(LogAuditoria.objects.get(pk=auditoria.pk).usuario_id), usuario["id"]
        )


class LogNaoGuardaDadoPessoalTests(LogImutavelBase):
    # Campos que identificam a pessoa diretamente. O usuário aparece no log
    # só pelo uuid (usuario_id).
    PROIBIDOS = {"nome", "email", "email_pendente", "telefone", "cpf", "senha"}

    def colunas(self, modelo):
        return {f.column for f in modelo._meta.concrete_fields}

    def test_tabelas_do_log_nao_tem_coluna_de_dado_pessoal(self):
        pessoais = {
            f.column
            for f in Usuario._meta.concrete_fields
            if f.column not in {"id", "criado_em", "atualizado_em", "ativo"}
        } | self.PROIBIDOS
        for modelo in (LogAcesso, LogAuditoria):
            with self.subTest(tabela=modelo._meta.db_table):
                self.assertEqual(self.colunas(modelo) & pessoais, set())
                self.assertFalse(
                    any("senha" in c or "email" in c for c in self.colunas(modelo))
                )

    def test_fluxo_real_nao_deixa_nome_email_telefone_ou_senha_no_log(self):
        email = f"{uuid.uuid4().hex[:8]}@akaer.com.br"
        dados, token = self.usuario_com(email)
        novo_nome = "Josefina A. Albuquerque"

        self.assertEqual(self.login(email, SENHA).status_code, 200)
        self.assertEqual(self.login(email, "senha-errada").status_code, 401)
        self.login("ninguem.cadastrado@akaer.com.br", "qualquer")
        resposta = self.client.patch(
            "/api/auth/me/",
            data=json.dumps({"nome": novo_nome}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resposta.status_code, 200)

        autor = {**dados}
        doc, _, _ = self.subir(autor=autor, identificador="DOC-LGPD")
        self.assertEqual(self.aprovar(doc).status_code, 200)
        self.assertEqual(self.consultar(doc, token).status_code, 200)

        # O fluxo gerou linhas nos dois logs…
        self.assertTrue(
            LogAuditoria.objects.filter(acao=AcaoAuditoria.LOGIN_FALHOU).exists()
        )
        self.assertTrue(LogAuditoria.objects.filter(acao=AcaoAuditoria.LOGIN).exists())
        self.assertTrue(LogAcesso.objects.filter(documento=doc).exists())

        # …e nenhuma delas tem dado pessoal, em nenhuma coluna.
        tudo = json.dumps(
            list(LogAuditoria.objects.values()) + list(LogAcesso.objects.values()),
            default=str,
        ).lower()
        for valor in (
            NOME,
            novo_nome,
            email,
            "ninguem.cadastrado@akaer.com.br",
            TELEFONE,
            SENHA,
            "senha-errada",
        ):
            with self.subTest(valor=valor):
                self.assertNotIn(valor.lower(), tudo)
        self.assertNotIn("@", tudo)

    def usuario_com(self, email):
        from core_api.services.auth.autenticacao import AuthService
        from core_api.utils.jwt_auth import generate_token

        dados = AuthService.create_user(
            {
                "email": email,
                "password": SENHA,
                "name": NOME,
                "role": "ENGENHARIA",
                "telefone": TELEFONE,
            }
        )
        return dados, generate_token(dados)
