"""Termos e consentimento (AKA-109).

A migração 0015 cadastra 2 termos obrigatórios e 1 opcional. O bloqueio por
termo pendente fica ligado nestes testes (nos outros, o conftest desliga).
"""

import json

from django.test import Client, TestCase, override_settings

from core_api.models import AcaoAuditoria, AceiteTermo, LogAuditoria, Termo
from core_api.services.auth.autenticacao import AuthService
from credenciais.models import Papel


class TermosBase(TestCase):
    databases = {"default", "credenciais_db"}

    @classmethod
    def setUpTestData(cls):
        cls.admin = AuthService.create_user(
            {
                "email": "admin@akaer.com.br",
                "password": "admin123",
                "name": "Admin",
                "role": Papel.ADMINISTRADOR,
            }
        )
        cls.eng = AuthService.create_user(
            {
                "email": "eng@akaer.com.br",
                "password": "eng123",
                "name": "Carlos",
                "role": Papel.ENGENHARIA,
            }
        )

    def setUp(self):
        self.enterContext(override_settings(TERMOS_EXIGIR_ACEITE=True))
        self.client = Client()

    def login(self, email="eng@akaer.com.br", senha="eng123"):
        resposta = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": email, "password": senha}),
            content_type="application/json",
        )
        return resposta.json()

    def auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def obrigatorios(self):
        return list(Termo.objects.filter(ativo=True, obrigatorio=True))

    def opcional(self):
        return Termo.objects.get(ativo=True, obrigatorio=False)

    def responder(self, token, respostas):
        return self.client.post(
            "/api/auth/termos/aceite/",
            data=json.dumps({"respostas": respostas}),
            content_type="application/json",
            **self.auth(token),
        )

    def aceitar_obrigatorios(self, token):
        return self.responder(
            token, [{"termo_id": t.id, "aceito": True} for t in self.obrigatorios()]
        )


class AceiteTests(TermosBase):
    def test_login_informa_que_ha_termos_pendentes(self):
        dados = self.login()

        self.assertIs(dados["termos_pendentes"], True)
        self.assertIs(dados["user"]["termos_pendentes"], True)

    def test_lista_os_3_termos_pendentes(self):
        token = self.login()["token"]

        resposta = self.client.get("/api/auth/termos-pendentes/", **self.auth(token))

        termos = resposta.json()["termos"]
        self.assertEqual(len(termos), 3)
        self.assertEqual(sum(t["obrigatorio"] for t in termos), 2)

    def test_sem_aceitar_os_obrigatorios_as_rotas_protegidas_respondem_403(self):
        token = self.login()["token"]

        resposta = self.client.get("/api/documents/meus/", **self.auth(token))

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(resposta.json()["codigo"], "TERMOS_PENDENTES")

    def test_aceite_fica_gravado_com_usuario_termo_versao_e_data(self):
        token = self.login()["token"]

        resposta = self.aceitar_obrigatorios(token)

        self.assertEqual(resposta.status_code, 200, resposta.content)
        for termo in self.obrigatorios():
            aceite = AceiteTermo.objects.get(termo=termo, usuario_id=self.eng["id"])
            self.assertTrue(aceite.aceito)
            self.assertEqual(aceite.versao, termo.versao)
            self.assertIsNotNone(aceite.criado_em)
        self.assertEqual(
            LogAuditoria.objects.filter(acao=AcaoAuditoria.TERMO_ACEITO).count(), 2
        )

    def test_depois_do_aceite_o_acesso_e_liberado(self):
        token = self.login()["token"]
        self.aceitar_obrigatorios(token)

        resposta = self.client.get("/api/documents/meus/", **self.auth(token))

        self.assertEqual(resposta.status_code, 200)

    def test_obrigatorio_nao_pode_ser_recusado(self):
        token = self.login()["token"]
        termo = self.obrigatorios()[0]

        resposta = self.responder(token, [{"termo_id": termo.id, "aceito": False}])

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(AceiteTermo.objects.exists())

    def test_quem_ja_aceitou_a_versao_atual_nao_ve_a_tela_de_novo(self):
        token = self.login()["token"]
        self.aceitar_obrigatorios(token)
        self.responder(token, [{"termo_id": self.opcional().id, "aceito": False}])

        self.assertIs(self.login()["termos_pendentes"], False)

    def test_opcional_recusado_nao_bloqueia_e_pode_ser_revogado(self):
        token = self.login()["token"]
        self.aceitar_obrigatorios(token)
        opcional = self.opcional()

        self.responder(token, [{"termo_id": opcional.id, "aceito": True}])
        self.responder(token, [{"termo_id": opcional.id, "aceito": False}])  # revoga

        meus = self.client.get("/api/auth/termos/", **self.auth(token)).json()["termos"]
        situacao = {t["id"]: t["aceito"] for t in meus}
        self.assertIs(situacao[opcional.id], False)
        self.assertEqual(
            self.client.get("/api/documents/meus/", **self.auth(token)).status_code, 200
        )
        # Revogar grava uma linha nova; a anterior continua lá.
        self.assertEqual(AceiteTermo.objects.filter(termo=opcional).count(), 2)

    def test_dados_do_titular_ficam_liberados_mesmo_com_pendencia(self):
        token = self.login()["token"]

        self.assertEqual(
            self.client.get("/api/auth/me/", **self.auth(token)).status_code, 200
        )

    def test_aceite_nao_pode_ser_alterado(self):
        token = self.login()["token"]
        self.aceitar_obrigatorios(token)
        aceite = AceiteTermo.objects.first()
        aceite.aceito = False

        with self.assertRaises(ValueError):
            aceite.save()


class CadastroTermoTests(TermosBase):
    def cadastrar(self, token, **dados):
        corpo = {"titulo": "Termo de Uso", "texto": "Texto novo", "versao": "3.0"}
        corpo.update(dados)
        return self.client.post(
            "/api/termos/",
            data=json.dumps(corpo),
            content_type="application/json",
            **self.auth(token),
        )

    def test_admin_cadastra_nova_versao_e_todos_respondem_de_novo(self):
        token_eng = self.login()["token"]
        self.aceitar_obrigatorios(token_eng)
        self.responder(token_eng, [{"termo_id": self.opcional().id, "aceito": True}])
        token_admin = self.login("admin@akaer.com.br", "admin123")["token"]

        resposta = self.cadastrar(token_admin, obrigatorio=True)

        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(
            Termo.objects.filter(titulo="Termo de Uso", ativo=True).count(), 1
        )
        self.assertIs(self.login()["termos_pendentes"], True)
        self.assertEqual(
            self.client.get("/api/documents/meus/", **self.auth(token_eng)).status_code,
            403,
        )
        self.assertTrue(
            LogAuditoria.objects.filter(
                acao=AcaoAuditoria.CRIADO, entidade="TERMO"
            ).exists()
        )

    def test_quem_nao_e_admin_nao_cadastra(self):
        token = self.login()["token"]

        self.assertEqual(self.cadastrar(token).status_code, 403)

    def test_campos_obrigatorios_do_cadastro(self):
        token_admin = self.login("admin@akaer.com.br", "admin123")["token"]
        self.aceitar_obrigatorios(token_admin)

        resposta = self.cadastrar(token_admin, texto="")

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json()["field"], "texto")
