"""
Testes do log de auditoria (AKA-31). Um teste por critério de aceite, mais
as regras da gravação automática.

TransactionTestCase pelo mesmo motivo da S2-33 (triggers deferrable de
documento só disparam no COMMIT) e com os dois bancos, porque o login e os
nomes vivem em `credenciais_db`.

"Subir" o documento é simulado com o ORM, gravando o que o upload grava
(Documento + área principal + ENVIADO no histórico, numa transação) com o
usuário atual definido: o upload que salva no banco chega com a AKA-59, e os
signals pegam qualquer save(), venha de onde vier.
"""

import json
import uuid

from django.db import transaction
from django.test import Client, TransactionTestCase

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    AcaoValidacao,
    Area,
    Categoria,
    Documento,
    DocumentoArea,
    EntidadeAuditoria,
    HistoricoValidacao,
    Idioma,
    LogAcesso,
    LogAuditoria,
    NivelSigilo,
    PerfilOperacional,
    Subcategoria,
    TipoDocumento,
)
from core_api.models.taxonomia import StatusDocumentoChoices
from core_api.services.auditoria import registrar_acao
from core_api.services.auth.autenticacao import AuthService
from core_api.utils.jwt_auth import generate_token
from core_api.utils.usuario_atual import com_usuario, get_usuario_atual

ADMIN = "ADMINISTRADOR"
DOC = EntidadeAuditoria.DOCUMENTO
AREA = EntidadeAuditoria.DOCUMENTO_AREA
HISTORICO = EntidadeAuditoria.HISTORICO_VALIDACAO
COMENTARIO = "Faltou anexar a revisão anterior do documento."


class AuditoriaBase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        self.autor, self.token_autor = self.usuario("ENGENHARIA", "Carlos Eduardo")
        self.admin, self.token_admin = self.usuario(ADMIN, "Ricardo Mendes")
        self.area = Area.objects.create(nome="Manutenção")
        categoria = Categoria.objects.create(area=self.area, nome="Normas")
        self.subcategoria = Subcategoria.objects.create(
            categoria=categoria, nome="Compatibilidade de Materiais"
        )
        self.tipo = TipoDocumento.objects.create(nome="Manual")
        self.nivel = NivelSigilo.objects.create(nome="INTERNO", ordinal=2)
        self.idioma = Idioma.objects.create(codigo="pt", nome="Português")

    # -- fábricas -------------------------------------------------------
    def usuario(self, role, nome):
        """Usuário real nos dois bancos e o token dele. Devolve o dict do
        AuthService (com nome e e-mail, para o teste de LGPD)."""
        dados = AuthService.create_user(
            {
                "email": f"{uuid.uuid4().hex[:8]}@akaer.com.br",
                "password": "x12345",
                "name": nome,
                "role": role,
                "telefone": "12 99999-0000",
            }
        )
        return dados, generate_token(dados)

    def novo_documento(self, perfil, identificador):
        """Documento + área principal, na transação de quem chama."""
        doc = Documento.objects.create(
            titulo="Compatibility of Materials Guideline",
            identificador_codigo=identificador,
            tipo_documento=self.tipo,
            numero_revisao="Rev. B",
            data_emissao="2026-03-10",
            subcategoria=self.subcategoria,
            status=StatusDocumentoChoices.VIGENTE,
            nivel_sigilo=self.nivel,
            nivel_sigilo_sugerido=self.nivel,
            origem_fonte="Biblioteca técnica",
            responsavel=perfil,
            idioma=self.idioma,
            arquivo_original_url="0f3b5c.pdf",
            usuario_upload=perfil,
        )
        doc_area = DocumentoArea.objects.create(
            documento=doc, area=self.area, is_principal=True
        )
        return doc, doc_area

    def subir(self, autor=None, identificador="MAT-COMP-001"):
        """Grava o que o upload grava, com o usuário atual = autor."""
        autor = autor or self.autor
        perfil = PerfilOperacional.objects.get(pk=autor["id"])
        with com_usuario(perfil.pk), transaction.atomic():
            doc, doc_area = self.novo_documento(perfil, identificador)
            envio = HistoricoValidacao.objects.create(
                documento=doc, acao=AcaoValidacao.ENVIADO, usuario=perfil
            )
        return doc, doc_area, envio

    # -- chamadas -------------------------------------------------------
    def post(self, url, corpo, token):
        return self.client.post(
            url,
            data=json.dumps(corpo),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def aprovar(self, doc):
        return self.post(f"/api/documents/{doc.id}/aprovar/", {}, self.token_admin)

    def devolver(self, doc):
        return self.post(
            f"/api/documents/{doc.id}/devolver/",
            {"comentario": COMENTARIO},
            self.token_admin,
        )

    def consultar(self, doc, token):
        return self.client.get(
            f"/api/documents/{doc.id}/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )

    def login(self, email, senha):
        return self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": email, "password": senha}),
            content_type="application/json",
        )

    def linhas(self, **filtros):
        """(usuario_id, acao, entidade, registro_id, campos) em ordem."""
        return [
            (
                str(log.usuario_id) if log.usuario_id else None,
                log.acao,
                log.entidade,
                log.registro_id,
                sorted(log.campos_alterados),
            )
            for log in LogAuditoria.objects.filter(**filtros).order_by("id")
        ]


class FluxoCompletoTestCase(AuditoriaBase):
    def test_log_depois_de_subir_aprovar_e_consultar_um_documento(self):
        doc, doc_area, envio = self.subir()
        self.assertEqual(self.aprovar(doc).status_code, 200)
        aprovacao = HistoricoValidacao.objects.get(
            documento=doc, acao=AcaoValidacao.APROVADO
        )
        antes_da_consulta = LogAuditoria.objects.count()
        self.assertEqual(self.consultar(doc, self.token_admin).status_code, 200)

        autor, admin = self.autor["id"], self.admin["id"]
        self.assertEqual(
            self.linhas(),
            [
                (autor, "CRIADO", DOC, str(doc.id), []),
                (autor, "CRIADO", AREA, str(doc_area.pk), []),
                (autor, "ENVIADO", HISTORICO, str(envio.pk), []),
                (
                    admin,
                    "ATUALIZADO",
                    DOC,
                    str(doc.id),
                    ["status_validacao", "validado_em", "validado_por"],
                ),
                (admin, "APROVADO", HISTORICO, str(aprovacao.pk), []),
            ],
        )
        # Consultar é visualização: vai para LogAcesso, não para a auditoria.
        self.assertEqual(LogAuditoria.objects.count(), antes_da_consulta)
        acesso = LogAcesso.objects.get(documento=doc)
        self.assertEqual(str(acesso.usuario_id), admin)
        self.assertEqual(acesso.acao, AcaoAcesso.VISUALIZACAO)

    def test_log_guarda_usuario_acao_registro_e_data_hora(self):
        doc, _, _ = self.subir()
        log = LogAuditoria.objects.get(entidade=DOC, acao=AcaoAuditoria.CRIADO)
        self.assertEqual(str(log.usuario_id), self.autor["id"])
        self.assertEqual(log.acao, "CRIADO")
        self.assertEqual(log.registro_id, str(doc.id))
        self.assertIsNotNone(log.criado_em)
        self.assertIsNotNone(log.criado_em.tzinfo)


class MudancaDeEstadoTestCase(AuditoriaBase):
    def test_toda_mudanca_de_estado_gera_log_ligado_ao_historico(self):
        devolvido, _, _ = self.subir(identificador="DOC-DEV")
        self.assertEqual(self.devolver(devolvido).status_code, 200)
        HistoricoValidacao.objects.create(
            documento=devolvido,
            acao=AcaoValidacao.REENVIADO,
            usuario=PerfilOperacional.objects.get(pk=self.autor["id"]),
        )
        aprovado, _, _ = self.subir(identificador="DOC-APR")
        self.assertEqual(self.aprovar(aprovado).status_code, 200)

        historico = list(HistoricoValidacao.objects.all())
        self.assertEqual(
            sorted(h.acao for h in historico),
            ["APROVADO", "DEVOLVIDO", "ENVIADO", "ENVIADO", "REENVIADO"],
        )
        for h in historico:
            with self.subTest(acao=h.acao):
                log = LogAuditoria.objects.get(
                    entidade=HISTORICO, registro_id=str(h.pk)
                )
                self.assertEqual(log.acao, h.acao)
                self.assertEqual(log.usuario_id, h.usuario_id)

    def test_comentario_do_validador_fica_no_historico_e_nunca_no_log(self):
        doc, _, _ = self.subir()
        self.assertEqual(self.devolver(doc).status_code, 200)
        devolucao = HistoricoValidacao.objects.get(acao=AcaoValidacao.DEVOLVIDO)
        self.assertEqual(devolucao.comentario, COMENTARIO)

        log = LogAuditoria.objects.get(acao=AcaoAuditoria.DEVOLVIDO)
        self.assertEqual(log.registro_id, str(devolucao.pk))
        self.assertEqual(log.campos_alterados, [])
        tudo = json.dumps(list(LogAuditoria.objects.values()), default=str)
        self.assertNotIn(COMENTARIO, tudo)
        # O campo do documento que guarda o motivo aparece só pelo nome.
        atualizacao = LogAuditoria.objects.get(acao=AcaoAuditoria.ATUALIZADO)
        self.assertIn("comentario_devolucao", atualizacao.campos_alterados)


class GravacaoAutomaticaTestCase(AuditoriaBase):
    def test_qualquer_save_e_delete_grava_sem_chamar_o_log(self):
        doc, doc_area, _ = self.subir()
        secundaria = DocumentoArea.objects.create(
            documento=doc, area=Area.objects.create(nome="Segurança")
        )
        secundaria_id = str(secundaria.pk)  # delete() zera o pk do objeto
        doc.titulo = "Título corrigido"
        doc.origem_fonte = "Fabricante"
        doc.save()
        secundaria.delete()

        self.assertEqual(
            self.linhas(registro_id=secundaria_id, entidade=AREA),
            [
                (None, "CRIADO", AREA, secundaria_id, []),
                (None, "EXCLUIDO", AREA, secundaria_id, []),
            ],
        )
        self.assertEqual(
            self.linhas(acao=AcaoAuditoria.ATUALIZADO),
            [(None, "ATUALIZADO", DOC, str(doc.id), ["origem_fonte", "titulo"])],
        )

    def test_excluir_documento_grava_log(self):
        perfil = PerfilOperacional.objects.get(pk=self.autor["id"])
        with transaction.atomic():
            doc, _ = self.novo_documento(perfil, "RASC-1")
        doc_id = str(doc.id)
        doc.delete()
        self.assertTrue(
            LogAuditoria.objects.filter(
                acao=AcaoAuditoria.EXCLUIDO, entidade=DOC, registro_id=doc_id
            ).exists()
        )

    def test_save_sem_mudanca_nao_gera_linha(self):
        doc, _, _ = self.subir()
        antes = LogAuditoria.objects.count()
        Documento.objects.get(pk=doc.pk).save()
        self.assertEqual(LogAuditoria.objects.count(), antes)

    def test_campos_alterados_guarda_so_os_nomes_nunca_os_valores(self):
        doc, _, _ = self.subir()
        doc.titulo = "Valor que não pode ir para o log"
        doc.save(update_fields=["titulo", "atualizado_em"])
        log = LogAuditoria.objects.get(acao=AcaoAuditoria.ATUALIZADO)
        self.assertEqual(log.campos_alterados, ["titulo"])
        tudo = json.dumps(list(LogAuditoria.objects.values()), default=str)
        self.assertNotIn("Valor que não pode ir para o log", tudo)

    def test_rollback_desfaz_tambem_o_log(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.subir()
                raise RuntimeError("desfaz tudo")
        self.assertEqual(Documento.objects.count(), 0)
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_usuario_vem_do_token_da_requisicao(self):
        doc, _, _ = self.subir()
        self.aprovar(doc)
        log = LogAuditoria.objects.get(acao=AcaoAuditoria.APROVADO)
        self.assertEqual(str(log.usuario_id), self.admin["id"])

    def test_usuario_atual_some_fora_da_requisicao(self):
        doc, _, _ = self.subir()
        self.aprovar(doc)
        self.assertIsNone(get_usuario_atual())

    def test_token_invalido_nao_vira_usuario_e_a_view_continua_barrando(self):
        doc, _, _ = self.subir()
        r = self.post(f"/api/documents/{doc.id}/aprovar/", {}, "token-falso")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(
            LogAuditoria.objects.filter(acao=AcaoAuditoria.APROVADO).exists()
        )


class LoginTestCase(AuditoriaBase):
    def test_login_com_sucesso_grava_login(self):
        self.assertEqual(self.login(self.autor["email"], "x12345").status_code, 200)
        self.assertEqual(
            self.linhas(acao=AcaoAuditoria.LOGIN),
            [(self.autor["id"], "LOGIN", "USUARIO", self.autor["id"], [])],
        )

    def test_senha_errada_grava_login_falhou_com_o_id(self):
        self.assertEqual(self.login(self.autor["email"], "errada").status_code, 401)
        self.assertEqual(
            self.linhas(acao=AcaoAuditoria.LOGIN_FALHOU),
            [(self.autor["id"], "LOGIN_FALHOU", "USUARIO", self.autor["id"], [])],
        )

    def test_email_inexistente_grava_login_falhou_sem_usuario(self):
        self.login("ninguem@akaer.com.br", "x")
        self.assertEqual(
            self.linhas(acao=AcaoAuditoria.LOGIN_FALHOU),
            [(None, "LOGIN_FALHOU", "USUARIO", None, [])],
        )


class SemDadosPessoaisTestCase(AuditoriaBase):
    def test_nenhum_nome_email_ou_telefone_aparece_no_log(self):
        doc, _, _ = self.subir()
        self.devolver(doc)
        self.login(self.autor["email"], "x12345")
        self.login(self.admin["email"], "errada")
        self.login("inexistente@akaer.com.br", "x")
        registrar_acao(
            self.autor["id"],
            AcaoAuditoria.DADOS_PESSOAIS_ALTERADOS,
            EntidadeAuditoria.USUARIO,
            self.autor["id"],
            ["nome", "telefone"],
        )

        tudo = json.dumps(list(LogAuditoria.objects.values()), default=str)
        proibidos = [
            "Carlos Eduardo",
            "Ricardo Mendes",
            self.autor["email"],
            self.admin["email"],
            "inexistente@akaer.com.br",
            "12 99999-0000",
            "@",
        ]
        for valor in proibidos:
            with self.subTest(valor=valor):
                self.assertNotIn(valor, tudo)

    def test_tabela_nao_tem_coluna_de_dado_pessoal(self):
        colunas = {f.name for f in LogAuditoria._meta.get_fields()}
        self.assertEqual(
            colunas,
            {
                "id",
                "usuario_id",
                "acao",
                "entidade",
                "registro_id",
                "campos_alterados",
                "criado_em",
            },
        )


class RegistrarAcaoTestCase(AuditoriaBase):
    def test_acao_e_entidade_fora_da_lista_sao_recusadas(self):
        with self.assertRaises(ValueError):
            registrar_acao(None, "QUALQUER_COISA", EntidadeAuditoria.USUARIO)
        with self.assertRaises(ValueError):
            registrar_acao(None, AcaoAuditoria.LOGIN, "TABELA_QUALQUER")
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_campos_so_aceitam_nomes(self):
        for campos in (["email=a@b.com"], ["nome completo"], [123]):
            with self.subTest(campos=campos):
                with self.assertRaises(ValueError):
                    registrar_acao(
                        None,
                        AcaoAuditoria.DADOS_PESSOAIS_ALTERADOS,
                        EntidadeAuditoria.USUARIO,
                        campos=campos,
                    )
        self.assertEqual(LogAuditoria.objects.count(), 0)

    def test_dados_pessoais_alterados_para_o_aka_57(self):
        log = registrar_acao(
            self.autor["id"],
            AcaoAuditoria.DADOS_PESSOAIS_ALTERADOS,
            EntidadeAuditoria.USUARIO,
            self.autor["id"],
            ["nome"],
        )
        log.refresh_from_db()
        self.assertEqual(log.campos_alterados, ["nome"])
        self.assertEqual(log.registro_id, self.autor["id"])
