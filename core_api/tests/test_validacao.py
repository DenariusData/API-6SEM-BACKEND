"""
Testes do fluxo de validação (S2-33): campos novos do Documento,
HistoricoValidacao (só inserção), regra do Confidencial no banco, regra do
ADMINISTRADOR e migration com documentos já existentes.

TransactionTestCase, não TestCase: os triggers deferrable de documento (área
principal) só disparam no COMMIT, e TestCase padrão nunca comita de verdade.
"""

import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from core_api.models import (
    AcaoValidacao,
    Documento,
    HistoricoImutavelError,
    HistoricoValidacao,
    NivelSigilo,
    PerfilOperacional,
    StatusProcessamento,
    StatusValidacao,
)
from core_api.tests.test_documento import _documento_valido


def _nivel(nome, ordinal):
    return NivelSigilo.objects.get_or_create(nome=nome, defaults={"ordinal": ordinal})[
        0
    ]


def _perfil():
    return PerfilOperacional.objects.create(
        usuario_id=uuid.uuid4(), matricula=f"AK-{uuid.uuid4().hex[:8]}"
    )


class DocumentoValidacaoCamposTestCase(TransactionTestCase):
    databases = {"default"}

    def test_documento_novo_comeca_aguardando_validacao(self):
        doc, _ = _documento_valido()
        doc.refresh_from_db()
        self.assertEqual(doc.status_validacao, StatusValidacao.AGUARDANDO_VALIDACAO)
        self.assertEqual(doc.status_processamento, StatusProcessamento.PENDENTE)
        self.assertIsNone(doc.validado_por)
        self.assertIsNone(doc.validado_em)
        self.assertIsNone(doc.comentario_devolucao)
        self.assertIsNone(doc.erro_processamento)

    def test_nivel_sugerido_e_final_sao_independentes(self):
        doc, _ = _documento_valido()
        outro = _nivel("RESTRITO", 3)
        doc.nivel_sigilo = outro
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.nivel_sigilo, outro)
        self.assertNotEqual(doc.nivel_sigilo_sugerido, outro)

    def test_disponiveis_so_traz_documentos_validados(self):
        aguardando, _ = _documento_valido(identificador_codigo="D-1")
        devolvido, _ = _documento_valido(identificador_codigo="D-2")
        disponivel, _ = _documento_valido(identificador_codigo="D-3")
        Documento.objects.filter(pk=devolvido.pk).update(
            status_validacao=StatusValidacao.DEVOLVIDO
        )
        Documento.objects.filter(pk=disponivel.pk).update(
            status_validacao=StatusValidacao.DISPONIVEL
        )

        ids = set(Documento.objects.disponiveis().values_list("pk", flat=True))
        self.assertEqual(ids, {disponivel.pk})
        # encadeia com outros filtros
        self.assertEqual(
            Documento.objects.disponiveis().filter(identificador_codigo="D-1").count(),
            0,
        )
        self.assertNotIn(aguardando.pk, ids)

    def test_validado_por_e_validado_em_andam_juntos(self):
        doc, _ = _documento_valido()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Documento.objects.filter(pk=doc.pk).update(validado_por=_perfil())
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Documento.objects.filter(pk=doc.pk).update(validado_em=timezone.now())


class RegraConfidencialNoBancoTestCase(TransactionTestCase):
    databases = {"default"}

    def _confidencial(self):
        conf = _nivel("CONFIDENCIAL", 4)
        doc, _ = _documento_valido(nivel_sigilo=conf, nivel_sigilo_sugerido=conf)
        return doc

    def test_confidencial_disponivel_sem_validador_e_recusado(self):
        doc = self._confidencial()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Documento.objects.filter(pk=doc.pk).update(
                    status_validacao=StatusValidacao.DISPONIVEL
                )

    def test_confidencial_nasce_disponivel_sem_validador_e_recusado(self):
        conf = _nivel("CONFIDENCIAL", 4)
        with self.assertRaises(IntegrityError):
            _documento_valido(
                nivel_sigilo=conf,
                nivel_sigilo_sugerido=conf,
                status_validacao=StatusValidacao.DISPONIVEL,
            )

    def test_recusa_vale_tambem_para_sql_direto(self):
        # Não depende do ORM nem do model: quem recusa é o banco.
        doc = self._confidencial()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE documento SET status_validacao = 'DISPONIVEL' "
                        "WHERE id = %s",
                        [doc.pk],
                    )

    def test_subir_o_nivel_para_confidencial_em_documento_disponivel_e_recusado(self):
        doc, _ = _documento_valido()
        Documento.objects.filter(pk=doc.pk).update(
            status_validacao=StatusValidacao.DISPONIVEL
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Documento.objects.filter(pk=doc.pk).update(
                    nivel_sigilo=_nivel("CONFIDENCIAL", 4)
                )

    def test_confidencial_disponivel_com_validador_e_aceito(self):
        doc = self._confidencial()
        Documento.objects.filter(pk=doc.pk).update(
            status_validacao=StatusValidacao.DISPONIVEL,
            validado_por=_perfil(),
            validado_em=timezone.now(),
        )
        doc.refresh_from_db()
        self.assertEqual(doc.status_validacao, StatusValidacao.DISPONIVEL)

    def test_outros_niveis_ficam_disponiveis_sem_validador(self):
        # É o caso dos documentos antigos migrados como DISPONIVEL.
        doc, _ = _documento_valido()
        Documento.objects.filter(pk=doc.pk).update(
            status_validacao=StatusValidacao.DISPONIVEL
        )
        doc.refresh_from_db()
        self.assertEqual(doc.status_validacao, StatusValidacao.DISPONIVEL)


class HistoricoValidacaoTestCase(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        self.doc, _ = _documento_valido()
        self.usuario = _perfil()

    def _linha(self, **kw):
        dados = dict(
            documento=self.doc, acao=AcaoValidacao.ENVIADO, usuario=self.usuario
        )
        dados.update(kw)
        return HistoricoValidacao.objects.create(**dados)

    def test_grava_quem_fez_e_quando(self):
        antes = timezone.now() - timedelta(seconds=5)
        linha = self._linha(comentario="primeiro envio")
        linha.refresh_from_db()
        self.assertEqual(linha.usuario, self.usuario)
        self.assertEqual(linha.acao, AcaoValidacao.ENVIADO)
        self.assertEqual(linha.comentario, "primeiro envio")
        self.assertGreater(linha.criado_em, antes)

    def test_nivel_sigilo_so_e_aceito_quando_aprovado(self):
        nivel = _nivel("INTERNO", 2)
        self._linha(acao=AcaoValidacao.APROVADO, nivel_sigilo=nivel)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._linha(acao=AcaoValidacao.DEVOLVIDO, nivel_sigilo=nivel)

    def test_model_recusa_editar_e_apagar(self):
        linha = self._linha()
        linha.comentario = "editado"
        with self.assertRaises(HistoricoImutavelError):
            linha.save()
        with self.assertRaises(HistoricoImutavelError):
            linha.delete()
        with self.assertRaises(HistoricoImutavelError):
            HistoricoValidacao.objects.filter(pk=linha.pk).update(comentario="x")
        with self.assertRaises(HistoricoImutavelError):
            HistoricoValidacao.objects.filter(pk=linha.pk).delete()

    def test_banco_recusa_update_e_delete_mesmo_por_sql_direto(self):
        linha = self._linha()
        for sql in (
            "UPDATE historico_validacao SET comentario = 'x' WHERE id = %s",
            "DELETE FROM historico_validacao WHERE id = %s",
        ):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(sql, [linha.pk])
        linha.refresh_from_db()
        self.assertEqual(linha.comentario, "")

    def test_apagar_documento_com_historico_e_bloqueado(self):
        from django.db.models import ProtectedError

        self._linha()
        with self.assertRaises(ProtectedError):
            self.doc.delete()


class MigrationComDocumentosExistentesTestCase(TransactionTestCase):
    """0005 roda em banco com documentos: antigos viram DISPONIVEL (exceto
    CONFIDENCIAL, que precisa de validação de verdade) e ganham o nível
    sugerido igual ao nível que já tinham."""

    databases = {"default"}
    ANTES = [("core_api", "0004_registro_pergunta")]
    DEPOIS = [("core_api", "0005_validacao_documento")]

    def _migrar(self, alvo):
        executor = MigrationExecutor(connection)
        executor.migrate(alvo)
        return MigrationExecutor(connection).loader.project_state(alvo).apps

    def tearDown(self):
        # deixa o banco de teste na migration mais recente para os outros testes
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_documentos_antigos_ficam_disponiveis(self):
        apps = self._migrar(self.ANTES)
        m = lambda nome: apps.get_model("core_api", nome)  # noqa: E731
        with transaction.atomic():
            area = m("Area").objects.create(nome="Manut")
            cat = m("Categoria").objects.create(area=area, nome="N")
            sub = m("Subcategoria").objects.create(categoria=cat, nome="S")
            tipo = m("TipoDocumento").objects.create(nome="Manual")
            interno = m("NivelSigilo").objects.create(nome="INTERNO", ordinal=2)
            conf = m("NivelSigilo").objects.create(nome="CONFIDENCIAL", ordinal=4)
            idioma = m("Idioma").objects.create(codigo="pt", nome="Português")
            perfil = m("PerfilOperacional").objects.create(
                usuario_id=uuid.uuid4(), matricula="AK-MIG"
            )
            for codigo, nivel in (
                ("ANT-1", interno),
                ("ANT-2", interno),
                ("ANT-3", conf),
            ):
                doc = m("Documento").objects.create(
                    titulo=codigo,
                    identificador_codigo=codigo,
                    tipo_documento=tipo,
                    numero_revisao="A",
                    data_emissao="2025-01-01",
                    subcategoria=sub,
                    nivel_sigilo=nivel,
                    origem_fonte="x",
                    responsavel=perfil,
                    idioma=idioma,
                    arquivo_original_url="x",
                    usuario_upload=perfil,
                )
                m("DocumentoArea").objects.create(
                    documento=doc, area=area, is_principal=True
                )

        apps = self._migrar(self.DEPOIS)
        Doc = apps.get_model("core_api", "Documento")
        por_codigo = {d.identificador_codigo: d for d in Doc.objects.all()}

        self.assertEqual(por_codigo["ANT-1"].status_validacao, "DISPONIVEL")
        self.assertEqual(por_codigo["ANT-2"].status_validacao, "DISPONIVEL")
        self.assertEqual(por_codigo["ANT-3"].status_validacao, "AGUARDANDO_VALIDACAO")
        for d in por_codigo.values():
            self.assertEqual(d.nivel_sigilo_sugerido_id, d.nivel_sigilo_id)
            self.assertEqual(d.status_processamento, "PENDENTE")

    def test_migration_em_banco_vazio(self):
        self._migrar(self.ANTES)
        apps = self._migrar(self.DEPOIS)
        self.assertEqual(apps.get_model("core_api", "Documento").objects.count(), 0)
