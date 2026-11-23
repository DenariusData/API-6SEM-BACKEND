"""Exclusão física da conta (AKA-110).

A linha do usuário some de credenciais_db; log e documentos ficam (só com o
uuid); perguntas e permissões do titular são apagadas.
"""

import json
import uuid

from django.core.management import call_command
from django.test import Client, TransactionTestCase

from core_api.models import (
    AcaoAuditoria,
    LogAuditoria,
    PerfilOperacional,
    RegistroPergunta,
    UsuarioPermissaoArea,
)
from core_api.services.auth.autenticacao import AuthService
from core_api.services.consulta_documentos import USUARIO_REMOVIDO, nome_exibido
from credenciais.models import Papel, Usuario


class ExclusaoContaTestCase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        call_command("seed_taxonomia", verbosity=0)
        self.client = Client()
        self.admin = AuthService.create_user(
            {
                "email": "admin@akaer.com.br",
                "password": "admin123",
                "name": "Admin",
                "role": Papel.ADMINISTRADOR,
            }
        )
        self.eng = AuthService.create_user(
            {
                "email": "eng@akaer.com.br",
                "password": "eng123",
                "name": "Carlos",
                "role": Papel.ENGENHARIA,
            }
        )
        self.eng_id = uuid.UUID(self.eng["id"])
        RegistroPergunta.objects.create(
            usuario_id=self.eng_id,
            pergunta="Meu CPF é 123, qual a norma?",
            encontrou=False,
            tempo_busca_ms=1,
            tempo_ia_ms=1,
            tempo_total_ms=2,
        )

    def token(self, email, senha):
        resposta = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": email, "password": senha}),
            content_type="application/json",
        )
        return resposta.json().get("token")

    def excluir_minha_conta(self, token, corpo):
        return self.client.delete(
            "/api/auth/me/",
            data=json.dumps(corpo),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def excluir_pelo_admin(self, usuario_id, token=None):
        token = token or self.token("admin@akaer.com.br", "admin123")
        return self.client.delete(
            f"/api/auth/users/{usuario_id}/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )

    def assert_excluido(self):
        self.assertFalse(
            Usuario.objects.using("credenciais_db").filter(id=self.eng_id).exists()
        )
        self.assertFalse(
            RegistroPergunta.objects.filter(usuario_id=self.eng_id).exists()
        )
        self.assertFalse(
            UsuarioPermissaoArea.objects.filter(usuario_id=self.eng_id).exists()
        )

    # -- pelo próprio titular --------------------------------------------
    def test_titular_exclui_a_propria_conta_com_a_senha(self):
        token = self.token("eng@akaer.com.br", "eng123")
        logs_antes = LogAuditoria.objects.filter(usuario_id=self.eng_id).count()

        resposta = self.excluir_minha_conta(token, {"senha": "eng123"})

        self.assertEqual(resposta.status_code, 204, resposta.content)
        self.assert_excluido()
        # O log do titular continua lá, e a exclusão foi registrada.
        self.assertGreaterEqual(
            LogAuditoria.objects.filter(usuario_id=self.eng_id).count(), logs_antes
        )
        self.assertTrue(
            LogAuditoria.objects.filter(
                acao=AcaoAuditoria.EXCLUIDO, registro_id=str(self.eng_id)
            ).exists()
        )

    def test_perfil_fica_sem_dados_do_titular(self):
        token = self.token("eng@akaer.com.br", "eng123")
        self.excluir_minha_conta(token, {"senha": "eng123"})

        perfil = PerfilOperacional.objects.get(usuario_id=self.eng_id)
        self.assertTrue(perfil.matricula.startswith("REMOVIDO-"))
        self.assertEqual(perfil.cargo, "")
        self.assertEqual(perfil.allowed_menus, [])

    def test_conta_excluida_nao_consegue_mais_entrar(self):
        token = self.token("eng@akaer.com.br", "eng123")
        self.excluir_minha_conta(token, {"senha": "eng123"})

        self.assertIsNone(self.token("eng@akaer.com.br", "eng123"))

    def test_senha_errada_nao_exclui(self):
        token = self.token("eng@akaer.com.br", "eng123")

        resposta = self.excluir_minha_conta(token, {"senha": "errada"})

        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(
            Usuario.objects.using("credenciais_db").filter(id=self.eng_id).exists()
        )

    def test_sem_senha_e_400(self):
        token = self.token("eng@akaer.com.br", "eng123")

        resposta = self.excluir_minha_conta(token, {})

        self.assertEqual(resposta.status_code, 400)

    # -- pelo administrador ----------------------------------------------
    def test_admin_exclui_outro_usuario(self):
        resposta = self.excluir_pelo_admin(self.eng_id)

        self.assertEqual(resposta.status_code, 204, resposta.content)
        self.assert_excluido()

    def test_admin_nao_exclui_a_propria_conta_por_esta_rota(self):
        resposta = self.excluir_pelo_admin(self.admin["id"])

        self.assertEqual(resposta.status_code, 400)

    def test_quem_nao_e_admin_nao_exclui_outros(self):
        token = self.token("eng@akaer.com.br", "eng123")

        resposta = self.excluir_pelo_admin(self.admin["id"], token)

        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(
            Usuario.objects.using("credenciais_db").filter(id=self.admin["id"]).exists()
        )

    def test_usuario_inexistente_e_404(self):
        self.assertEqual(self.excluir_pelo_admin(uuid.uuid4()).status_code, 404)

    # -- nome nas telas --------------------------------------------------
    def test_id_sem_usuario_aparece_como_usuario_removido(self):
        self.excluir_pelo_admin(self.eng_id)

        self.assertEqual(nome_exibido({}, self.eng_id), USUARIO_REMOVIDO)
        self.assertIsNone(nome_exibido({}, None))
