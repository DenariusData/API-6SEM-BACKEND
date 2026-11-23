"""Cadastro de usuário pela tela de Gestão de Usuários (fix AKA-104).

A tela envia o perfil pelo nome mostrado ("Engenharia"). O usuário novo
precisa sair com o papel certo e com as permissões de área do papel, senão
não enxerga documento nenhum nem a tela de pergunta da IA.
"""

import json

from django.core.management import call_command
from django.test import Client, TestCase

from core_api.models import PerfilOperacional, UsuarioPermissaoArea
from core_api.services.auth.autenticacao import (
    PERMISSOES_PADRAO_POR_PAPEL,
    AuthService,
    CadastroInvalido,
    normalizar_papel,
)
from credenciais.models import Papel, Usuario


class CadastroUsuarioTestCase(TestCase):
    databases = {"default", "credenciais_db"}

    @classmethod
    def setUpTestData(cls):
        call_command("seed_taxonomia", verbosity=0)
        AuthService.create_user(
            {
                "email": "admin@akaer.com.br",
                "password": "admin123",
                "name": "Ricardo Mendes",
                "role": Papel.ADMINISTRADOR,
            }
        )

    def setUp(self):
        self.client = Client()
        resposta = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "admin@akaer.com.br", "password": "admin123"}),
            content_type="application/json",
        )
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {resposta.json()['token']}"}

    def _cadastrar(self, **dados):
        payload = {"name": "Teste Review", "email": "teste.review@akaer.com.br"}
        payload.update(dados)
        return self.client.post(
            "/api/auth/users/",
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth,
        )

    def _perfil(self, email):
        usuario = Usuario.objects.using("credenciais_db").get(email=email)
        return usuario, PerfilOperacional.objects.get(usuario_id=usuario.id)

    def test_perfil_com_o_nome_da_tela_vira_o_codigo_do_papel(self):
        resposta = self._cadastrar(role="Engenharia")

        self.assertEqual(resposta.status_code, 201)
        usuario, _ = self._perfil("teste.review@akaer.com.br")
        self.assertEqual(usuario.papel_id, Papel.ENGENHARIA)
        self.assertFalse(
            Papel.objects.using("credenciais_db").filter(codigo="Engenharia").exists()
        )

    def test_usuario_novo_recebe_as_permissoes_de_area_do_papel(self):
        self._cadastrar(role="Qualidade")

        _, perfil = self._perfil("teste.review@akaer.com.br")
        concedidas = set(
            UsuarioPermissaoArea.objects.filter(usuario=perfil).values_list(
                "area__nome", "nivel_sigilo_maximo__nome"
            )
        )
        self.assertEqual(concedidas, set(PERMISSOES_PADRAO_POR_PAPEL[Papel.QUALIDADE]))

    def test_usuario_novo_entra_com_o_papel_e_os_menus_do_papel(self):
        self._cadastrar(role="Engenharia", password="senha123")

        resposta = self.client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"email": "teste.review@akaer.com.br", "password": "senha123"}
            ),
            content_type="application/json",
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["user"]["role_code"], Papel.ENGENHARIA)
        self.assertIn("Documentos", resposta.json()["user"]["allowed_menus"])

    def test_email_repetido_e_recusado_sem_erro_no_servidor(self):
        self._cadastrar(role="Engenharia")

        resposta = self._cadastrar(role="Engenharia", email="TESTE.review@akaer.com.br")

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("e-mail", resposta.json()["detail"])
        self.assertEqual(
            Usuario.objects.using("credenciais_db")
            .filter(email__iexact="teste.review@akaer.com.br")
            .count(),
            1,
        )

    def test_perfil_desconhecido_e_recusado(self):
        resposta = self._cadastrar(role="Diretor")

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(
            Usuario.objects.using("credenciais_db")
            .filter(email="teste.review@akaer.com.br")
            .exists()
        )


class NormalizarPapelTestCase(TestCase):
    def test_aceita_codigo_e_nome_sem_diferenciar_caixa(self):
        self.assertEqual(normalizar_papel("ENGENHARIA"), Papel.ENGENHARIA)
        self.assertEqual(normalizar_papel("qualidade"), Papel.QUALIDADE)
        self.assertEqual(normalizar_papel(" Administrador "), Papel.ADMINISTRADOR)

    def test_recusa_papel_desconhecido(self):
        with self.assertRaises(CadastroInvalido):
            normalizar_papel("Gerente")
