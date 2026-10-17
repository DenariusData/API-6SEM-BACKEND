"""
Testes dos endpoints de aprovar e devolver documento (S2-34).

Um teste por regra. TransactionTestCase pelo mesmo motivo da S2-33: os
triggers deferrable de documento só disparam no COMMIT.
"""

import json
import uuid
from unittest import mock

from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core_api.models import (
    AcaoValidacao,
    HistoricoValidacao,
    NivelSigilo,
    PerfilOperacional,
    StatusValidacao,
)
from core_api.services import validacao
from core_api.tests.test_documento import _documento_valido
from core_api.utils.jwt_auth import generate_token

ADMIN = "ADMINISTRADOR"


def _nivel(nome, ordinal):
    return NivelSigilo.objects.get_or_create(nome=nome, defaults={"ordinal": ordinal})[
        0
    ]


def _usuario(role):
    """PerfilOperacional + token JWT do mesmo usuário (sub == usuario_id)."""
    perfil = PerfilOperacional.objects.create(
        usuario_id=uuid.uuid4(), matricula=f"AK-{uuid.uuid4().hex[:8]}"
    )
    token = generate_token(
        {
            "id": str(perfil.usuario_id),
            "email": f"{role.lower()}@akaer",
            "role_code": role,
        }
    )
    return perfil, token


# Estes testes cobrem só as regras da aprovação. O processamento para a IA
# que a aprovação dispara (AKA-62) tem os próprios testes, em
# test_processamento.py.
@override_settings(PROCESSAMENTO_AUTOMATICO=False)
class EndpointsValidacaoBase(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        self.client = Client()
        self.admin, self.token_admin = _usuario(ADMIN)
        self.doc, _ = _documento_valido()
        self.autor = self.doc.usuario_upload  # quem fez o upload
        self.restrito = _nivel("RESTRITO", 3)
        self.confidencial = _nivel("CONFIDENCIAL", 4)

    def post(self, acao, corpo=None, token=None, doc=None, raw=None):
        doc = doc or self.doc
        token = self.token_admin if token is None else token
        return self.client.post(
            f"/api/documents/{doc.id}/{acao}/",
            data=raw if raw is not None else json.dumps(corpo or {}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}" if token else "",
        )

    def aprovar(self, corpo=None, **kw):
        return self.post("aprovar", corpo, **kw)

    def devolver(self, corpo=None, **kw):
        return self.post("devolver", corpo, **kw)

    def historico(self, doc=None):
        return list(
            HistoricoValidacao.objects.filter(documento=doc or self.doc).values_list(
                "acao", flat=True
            )
        )

    def assertNaoMudou(self, doc=None):
        doc = doc or self.doc
        doc.refresh_from_db()
        self.assertEqual(doc.status_validacao, StatusValidacao.AGUARDANDO_VALIDACAO)
        self.assertIsNone(doc.validado_por)
        self.assertIsNone(doc.validado_em)
        self.assertEqual(self.historico(doc), [])


class AprovarTestCase(EndpointsValidacaoBase):
    def test_aprovar_grava_disponivel_nivel_final_quem_e_quando(self):
        r = self.aprovar({"nivel_sigilo": "RESTRITO"})
        self.assertEqual(r.status_code, 200, r.content)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status_validacao, StatusValidacao.DISPONIVEL)
        self.assertEqual(self.doc.nivel_sigilo, self.restrito)
        self.assertEqual(self.doc.validado_por, self.admin)
        self.assertIsNotNone(self.doc.validado_em)
        # a sugestão do upload continua guardada, diferente do nível final
        self.assertNotEqual(self.doc.nivel_sigilo_sugerido, self.restrito)
        corpo = r.json()
        self.assertEqual(corpo["status_validacao"], "DISPONIVEL")
        self.assertEqual(corpo["nivel_sigilo"], "RESTRITO")
        self.assertEqual(corpo["validado_por"], str(self.admin.pk))

    def test_aprovar_sem_nivel_mantem_o_nivel_atual(self):
        nivel_antes = self.doc.nivel_sigilo
        r = self.aprovar()  # sem corpo nenhum
        self.assertEqual(r.status_code, 200, r.content)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.nivel_sigilo, nivel_antes)
        self.assertEqual(self.doc.status_validacao, StatusValidacao.DISPONIVEL)

    def test_nivel_inexistente_ou_invalido_e_400(self):
        for valor in ("NAO-EXISTE", "", "   ", 3, ["RESTRITO"]):
            r = self.aprovar({"nivel_sigilo": valor})
            self.assertEqual(r.status_code, 400, valor)
            self.assertEqual(r.json()["field"], "nivel_sigilo")
        self.assertNaoMudou()

    def test_aprovacao_aparece_no_historico_com_quem_quando_e_nivel(self):
        self.aprovar({"nivel_sigilo": "RESTRITO"})
        linha = HistoricoValidacao.objects.get(documento=self.doc)
        self.assertEqual(linha.acao, AcaoValidacao.APROVADO)
        self.assertEqual(linha.usuario, self.admin)
        self.assertEqual(linha.nivel_sigilo, self.restrito)
        self.assertIsNotNone(linha.criado_em)


class DevolverTestCase(EndpointsValidacaoBase):
    def test_devolver_grava_estado_comentario_e_historico(self):
        r = self.devolver({"comentario": "Falta o número da revisão."})
        self.assertEqual(r.status_code, 200, r.content)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status_validacao, StatusValidacao.DEVOLVIDO)
        self.assertEqual(self.doc.comentario_devolucao, "Falta o número da revisão.")
        self.assertIsNone(self.doc.validado_por)  # devolver não valida
        linha = HistoricoValidacao.objects.get(documento=self.doc)
        self.assertEqual(linha.acao, AcaoValidacao.DEVOLVIDO)
        self.assertEqual(linha.usuario, self.admin)
        self.assertEqual(linha.comentario, "Falta o número da revisão.")
        self.assertIsNone(linha.nivel_sigilo)

    def test_devolver_sem_comentario_ou_com_menos_de_10_caracteres_e_400(self):
        casos = [
            {},
            {"comentario": ""},
            {"comentario": "          "},  # só espaços
            {"comentario": "curto"},
            {"comentario": "  123456789  "},  # 9 depois do strip
            {"comentario": None},
            {"comentario": 12345678901},  # não é texto
        ]
        for corpo in casos:
            r = self.devolver(corpo)
            self.assertEqual(r.status_code, 400, corpo)
            self.assertEqual(r.json()["field"], "comentario")
        self.assertNaoMudou()

    def test_comentario_com_exatamente_10_caracteres_passa(self):
        r = self.devolver({"comentario": "1234567890"})
        self.assertEqual(r.status_code, 200, r.content)


class PermissaoTestCase(EndpointsValidacaoBase):
    def test_quem_nao_e_administrador_recebe_403_nos_dois_endpoints(self):
        for papel in ("ENGENHARIA", "QUALIDADE"):
            _, token = _usuario(papel)
            r1 = self.aprovar(token=token)
            r2 = self.devolver(
                {"comentario": "motivo suficientemente longo"}, token=token
            )
            self.assertEqual(r1.status_code, 403, papel)
            self.assertEqual(r2.status_code, 403, papel)
        self.assertNaoMudou()

    def test_sem_token_e_401(self):
        self.assertEqual(self.aprovar(token="").status_code, 401)
        self.assertEqual(self.devolver(token="").status_code, 401)
        self.assertNaoMudou()

    def test_autor_recebe_403_ao_aprovar_o_proprio_documento(self):
        # mesmo sendo ADMINISTRADOR: o token é de um admin que é o autor
        token_autor = generate_token(
            {
                "id": str(self.autor.usuario_id),
                "email": "autor@akaer",
                "role_code": ADMIN,
            }
        )
        r = self.aprovar(token=token_autor)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(
            r.json()["detail"], "Você não pode aprovar um documento enviado por você"
        )
        self.assertNaoMudou()

    def test_autor_recebe_403_ao_devolver_o_proprio_documento(self):
        token_autor = generate_token(
            {
                "id": str(self.autor.usuario_id),
                "email": "autor@akaer",
                "role_code": ADMIN,
            }
        )
        r = self.devolver({"comentario": "devolvendo o meu próprio"}, token=token_autor)
        self.assertEqual(r.status_code, 403)
        self.assertNaoMudou()

    def test_se_o_autor_e_administrador_outro_administrador_valida(self):
        # o doc é do autor (admin); o admin da vez é outra pessoa
        r = self.aprovar()
        self.assertEqual(r.status_code, 200, r.content)

    def test_usuario_sem_perfil_operacional_recebe_403(self):
        token = generate_token(
            {"id": str(uuid.uuid4()), "email": "x@akaer", "role_code": ADMIN}
        )
        self.assertEqual(self.aprovar(token=token).status_code, 403)
        self.assertNaoMudou()


class EstadoTestCase(EndpointsValidacaoBase):
    def test_documento_ja_aprovado_recebe_409(self):
        self.assertEqual(self.aprovar().status_code, 200)
        self.assertEqual(self.aprovar().status_code, 409)  # aprovar de novo
        r = self.devolver({"comentario": "tarde demais para devolver"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.historico(), [AcaoValidacao.APROVADO])  # só 1 linha

    def test_documento_ja_devolvido_recebe_409(self):
        self.assertEqual(
            self.devolver({"comentario": "primeira devolução ok"}).status_code, 200
        )
        self.assertEqual(self.aprovar().status_code, 409)
        r = self.devolver({"comentario": "devolver de novo, não pode"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.historico(), [AcaoValidacao.DEVOLVIDO])

    def test_documento_inexistente_e_404(self):
        class Fake:
            id = uuid.uuid4()

        self.assertEqual(self.aprovar(doc=Fake()).status_code, 404)
        self.assertEqual(
            self.devolver({"comentario": "x" * 12}, doc=Fake()).status_code, 404
        )


class ConfidencialTestCase(EndpointsValidacaoBase):
    def test_aprovar_como_confidencial_sem_confirmar_e_400(self):
        for corpo in (
            {"nivel_sigilo": "CONFIDENCIAL"},
            {"nivel_sigilo": "CONFIDENCIAL", "confirmar_confidencial": False},
            {"nivel_sigilo": "CONFIDENCIAL", "confirmar_confidencial": "true"},
            {"nivel_sigilo": "CONFIDENCIAL", "confirmar_confidencial": 1},
            {"nivel_sigilo": "confidencial"},  # nome sem diferenciar caixa
        ):
            r = self.aprovar(corpo)
            self.assertEqual(r.status_code, 400, corpo)
            self.assertEqual(r.json()["field"], "confirmar_confidencial")
        self.assertNaoMudou()

    def test_aprovar_como_confidencial_confirmando_funciona(self):
        r = self.aprovar(
            {"nivel_sigilo": "CONFIDENCIAL", "confirmar_confidencial": True}
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status_validacao, StatusValidacao.DISPONIVEL)
        self.assertEqual(self.doc.nivel_sigilo, self.confidencial)
        self.assertEqual(self.doc.validado_por, self.admin)  # exigido pelo banco
        self.assertIsNotNone(self.doc.validado_em)

    def test_documento_que_ja_e_confidencial_tambem_exige_confirmacao(self):
        doc, _ = _documento_valido(
            identificador_codigo="CONF-1",
            nivel_sigilo=self.confidencial,
            nivel_sigilo_sugerido=self.confidencial,
        )
        self.assertEqual(self.aprovar(doc=doc).status_code, 400)  # sem corrigir nível
        self.assertNaoMudou(doc)
        self.assertEqual(
            self.aprovar({"confirmar_confidencial": True}, doc=doc).status_code, 200
        )

    def test_confirmar_nao_e_exigido_para_outros_niveis(self):
        r = self.aprovar({"nivel_sigilo": "RESTRITO"})
        self.assertEqual(r.status_code, 200)

    def test_rebaixar_de_confidencial_nao_exige_confirmacao(self):
        doc, _ = _documento_valido(
            identificador_codigo="CONF-2",
            nivel_sigilo=self.confidencial,
            nivel_sigilo_sugerido=self.confidencial,
        )
        r = self.aprovar({"nivel_sigilo": "RESTRITO"}, doc=doc)
        self.assertEqual(r.status_code, 200, r.content)


class TransacaoTestCase(EndpointsValidacaoBase):
    def test_falha_ao_gravar_o_historico_desfaz_a_mudanca_de_estado(self):
        client = Client(raise_request_exception=False)
        with mock.patch.object(
            HistoricoValidacao.objects, "create", side_effect=RuntimeError("boom")
        ):
            r = client.post(
                f"/api/documents/{self.doc.id}/aprovar/",
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token_admin}",
            )
        self.assertEqual(r.status_code, 500)
        self.assertNaoMudou()

    def test_documento_e_travado_com_select_for_update(self):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.aprovar().status_code, 200)
        self.assertTrue(
            any("FOR UPDATE" in q["sql"] for q in ctx.captured_queries),
            "o documento deveria ser lido com SELECT ... FOR UPDATE",
        )


class CorpoEMetodoTestCase(EndpointsValidacaoBase):
    def test_json_invalido_e_400(self):
        for raw in ("{nao e json", "[1, 2]", '"texto"'):
            self.assertEqual(self.aprovar(raw=raw).status_code, 400, raw)
            self.assertEqual(self.devolver(raw=raw).status_code, 400, raw)
        self.assertNaoMudou()

    def test_so_aceita_post(self):
        url = f"/api/documents/{self.doc.id}/aprovar/"
        r = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token_admin}")
        self.assertEqual(r.status_code, 405)

    def test_cada_chamada_trata_um_unico_documento(self):
        # Não existe aprovação em lote: ids extras no corpo são ignorados.
        outro, _ = _documento_valido(identificador_codigo="OUTRO-1")
        r = self.aprovar({"ids": [str(outro.id)], "documentos": [str(outro.id)]})
        self.assertEqual(r.status_code, 200)
        self.assertNaoMudou(outro)


class ServicoSegundaCamadaTestCase(EndpointsValidacaoBase):
    """O decorator da view já barra quem não é ADMINISTRADOR; a service repete
    a checagem para que nenhum outro chamador (comando, task, outra view)
    consiga pular a regra."""

    def test_service_recusa_quem_nao_e_administrador(self):
        for papel in ("ENGENHARIA", "QUALIDADE", None, ""):
            with self.assertRaises(validacao.PermissaoNegada):
                validacao.aprovar(self.doc.id, self.admin, papel)
            with self.assertRaises(validacao.PermissaoNegada):
                validacao.devolver(self.doc.id, self.admin, papel, "motivo longo ok")
        self.assertNaoMudou()
