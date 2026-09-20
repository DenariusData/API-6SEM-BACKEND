import json
from django.test import TestCase, Client
from core_api.views.auth_views import UNIFIED_AUTH_ERROR_MSG


class AuthTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def test_login_success_engenharia(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "engenharia@akaer.com.br", "password": "eng123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("user", data)
        self.assertEqual(data["user"]["role"], "Engenharia")
        self.assertIn("AI Command Assistant", data["user"]["allowed_menus"])
        self.assertIn("Solicitar OI", data["user"]["allowed_menus"])

    def test_login_success_qualidade(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "qualidade@akaer.com.br", "password": "qual123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["user"]["role"], "Qualidade")
        self.assertIn("Relatórios de Qualidade", data["user"]["allowed_menus"])
        self.assertIn("Auditoria & Conformidade", data["user"]["allowed_menus"])

    def test_login_success_admin(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "admin@akaer.com.br", "password": "admin123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        user = data["user"]
        self.assertEqual(user["role"], "Administrador")
        self.assertIn("Gestão de Usuários", user["allowed_menus"])
        self.assertIn("Importar Arquivos", user["allowed_menus"])
        self.assertIn("Classificar Categorias", user["allowed_menus"])
        self.assertIn("AI Command Assistant", user["allowed_menus"])

    def test_login_invalid_password(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "admin@akaer.com.br", "password": "senha_errada"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], UNIFIED_AUTH_ERROR_MSG)

    def test_login_nonexistent_user(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps(
                {"email": "desconhecido@akaer.com.br", "password": "123"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], UNIFIED_AUTH_ERROR_MSG)

    def test_login_invalid_domain(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "usuario@outrodomino.com", "password": "123"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data["detail"], UNIFIED_AUTH_ERROR_MSG)

    def test_user_management_list(self):
        response = self.client.get("/api/auth/users/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreaterEqual(data["count"], 3)

    def test_user_management_create(self):
        new_user_payload = {
            "email": "novo.engenheiro@akaer.com.br",
            "password": "pass",
            "name": "Novo Engenheiro",
            "role": "Engenharia",
        }
        response = self.client.post(
            "/api/auth/users/",
            data=json.dumps(new_user_payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["user"]["email"], "novo.engenheiro@akaer.com.br")
