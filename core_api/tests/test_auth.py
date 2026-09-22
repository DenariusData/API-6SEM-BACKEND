import json

from django.test import Client, TestCase

from core_api.services.auth.autenticacao import AuthService
from core_api.views.auth_views import UNIFIED_AUTH_ERROR_MSG
from credenciais.models import Papel

USUARIOS_TESTE = [
    {
        "email": "engenharia@akaer.com.br",
        "password": "eng123",
        "name": "Carlos Eduardo",
        "role": Papel.ENGENHARIA,
    },
    {
        "email": "qualidade@akaer.com.br",
        "password": "qual123",
        "name": "Ana Souza",
        "role": Papel.QUALIDADE,
    },
    {
        "email": "admin@akaer.com.br",
        "password": "admin123",
        "name": "Ricardo Mendes",
        "role": Papel.ADMINISTRADOR,
    },
]


class AuthTestCase(TestCase):
    # Por padrão, TestCase só libera queries no banco 'default'. Como o
    # login consulta o banco isolado 'credenciais_db' (app credenciais),
    # precisa declarar explicitamente que este teste também usa esse
    # segundo banco -- senão o Django bloqueia a query de propósito.
    databases = {"default", "credenciais_db"}

    @classmethod
    def setUpTestData(cls):
        # O teste cria os próprios usuários -- não depende de rodar
        # `manage.py seed_usuarios_demo` à mão antes. O banco de teste é
        # sempre criado vazio (só com as migrations aplicadas), então
        # qualquer dado que o teste precise tem que vir de dentro dele.
        for dados in USUARIOS_TESTE:
            AuthService.create_user(dados)

    def setUp(self):
        self.client = Client()

    def _login(self, email, password):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": email, "password": password}),
            content_type="application/json",
        )
        return response

    def _admin_token(self):
        response = self._login("admin@akaer.com.br", "admin123")
        return response.json()["token"]

    def test_login_success_engenharia(self):
        response = self._login("engenharia@akaer.com.br", "eng123")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("user", data)
        self.assertIn("token", data)
        self.assertEqual(data["user"]["role"], "ENGENHARIA")
        self.assertIn("AI Command Assistant", data["user"]["allowed_menus"])
        self.assertIn("Solicitar OI", data["user"]["allowed_menus"])

    def test_login_success_qualidade(self):
        response = self._login("qualidade@akaer.com.br", "qual123")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["user"]["role"], "QUALIDADE")
        self.assertIn("Relatorios de Qualidade", data["user"]["allowed_menus"])
        self.assertIn("Auditoria & Conformidade", data["user"]["allowed_menus"])

    def test_login_success_admin(self):
        response = self._login("admin@akaer.com.br", "admin123")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        user = data["user"]
        self.assertEqual(user["role"], "ADMINISTRADOR")
        self.assertIn("Gestao de Usuarios", user["allowed_menus"])
        self.assertIn("Importar Arquivos", user["allowed_menus"])
        self.assertIn("Classificar Categorias", user["allowed_menus"])
        self.assertIn("AI Command Assistant", user["allowed_menus"])

    def test_login_invalid_password(self):
        response = self._login("admin@akaer.com.br", "senha_errada")
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], UNIFIED_AUTH_ERROR_MSG)

    def test_login_nonexistent_user(self):
        response = self._login("desconhecido@akaer.com.br", "123")
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], UNIFIED_AUTH_ERROR_MSG)

    def test_login_invalid_domain(self):
        response = self._login("usuario@outrodomino.com", "123")
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], UNIFIED_AUTH_ERROR_MSG)

    # -- profile_view: identidade vem do token, não de parâmetro de URL --

    def test_profile_requires_token(self):
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 401)

    def test_profile_returns_logged_user(self):
        token = self._admin_token()
        response = self.client.get(
            "/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["role"], "ADMINISTRADOR")

    # -- user_management_view: só Administrador autenticado --

    def test_user_management_list_requires_token(self):
        response = self.client.get("/api/auth/users/")
        self.assertEqual(response.status_code, 401)

    def test_user_management_list_rejects_non_admin(self):
        response_login = self._login("engenharia@akaer.com.br", "eng123")
        token = response_login.json()["token"]
        response = self.client.get(
            "/api/auth/users/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 403)

    def test_user_management_list_with_admin_token(self):
        token = self._admin_token()
        response = self.client.get(
            "/api/auth/users/", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(data["count"], 3)

    def test_user_management_create_with_admin_token(self):
        token = self._admin_token()
        new_user_payload = {
            "email": "novo.engenheiro@akaer.com.br",
            "password": "pass",
            "name": "Novo Engenheiro",
            "role": "ENGENHARIA",
        }
        response = self.client.post(
            "/api/auth/users/",
            data=json.dumps(new_user_payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["user"]["email"], "novo.engenheiro@akaer.com.br")
