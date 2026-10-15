import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from core_api.utils.jwt_auth import generate_token

USUARIO = {
    "id": str(uuid.uuid4()),
    "email": "engenharia@akaer.com.br",
    "role_code": "ENGENHARIA",
}


class UploadDocumentTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.token = generate_token(USUARIO)
        self.headers = {
            "HTTP_AUTHORIZATION": f"Bearer {self.token}",
        }

    def _upload(
        self,
        *,
        file=None,
        title="Documento de teste",
        category="Qualidade",
        description="Descrição do documento",
        authenticated=True,
    ):
        data = {
            "title": title,
            "category": category,
            "description": description,
        }

        if file is not None:
            data["file"] = file

        headers = self.headers if authenticated else {}

        return self.client.post(
            "/api/documents/upload/",
            data=data,
            **headers,
        )

    def test_upload_sucesso(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            with patch(
                "core_api.views.document_views.settings.BASE_DIR",
                temp_dir,
            ):
                arquivo = SimpleUploadedFile(
                    "teste.pdf",
                    b"conteudo do PDF de teste",
                    content_type="application/pdf",
                )

                response = self._upload(file=arquivo)

                self.assertEqual(response.status_code, 201)

                data = response.json()

                self.assertEqual(
                    data["message"],
                    "Arquivo enviado com sucesso!",
                )
                self.assertEqual(
                    data["document"]["name"],
                    "teste.pdf",
                )
                self.assertEqual(
                    data["document"]["saved_name"],
                    "teste.pdf",
                )
                self.assertEqual(
                    data["document"]["title"],
                    "Documento de teste",
                )
                self.assertEqual(
                    data["document"]["category"],
                    "Qualidade",
                )
                self.assertNotIn("path", data["document"])

                self.assertTrue((Path(temp_dir) / "uploads" / "teste.pdf").exists())

    def test_upload_sem_token(self):
        arquivo = SimpleUploadedFile(
            "teste.pdf",
            b"conteudo",
            content_type="application/pdf",
        )

        response = self._upload(
            file=arquivo,
            authenticated=False,
        )

        self.assertEqual(response.status_code, 401)

    def test_upload_sem_arquivo(self):
        response = self._upload()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Nenhum arquivo foi enviado.",
        )

    def test_upload_sem_titulo(self):
        arquivo = SimpleUploadedFile(
            "teste.pdf",
            b"conteudo",
            content_type="application/pdf",
        )

        response = self._upload(
            file=arquivo,
            title="",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "O título do documento é obrigatório.",
        )

    def test_upload_sem_categoria(self):
        arquivo = SimpleUploadedFile(
            "teste.pdf",
            b"conteudo",
            content_type="application/pdf",
        )

        response = self._upload(
            file=arquivo,
            category="",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "A categoria do documento é obrigatória.",
        )

    def test_upload_extensao_invalida(self):
        arquivo = SimpleUploadedFile(
            "teste.png",
            b"conteudo",
            content_type="image/png",
        )

        response = self._upload(file=arquivo)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "Tipo de arquivo não permitido. Envie PDF, DOC, DOCX, XLS, XLSX ou TXT.",
        )

    def test_upload_arquivo_maior_que_10_mb(self):
        arquivo = SimpleUploadedFile(
            "teste.pdf",
            b"0" * (10 * 1024 * 1024 + 1),
            content_type="application/pdf",
        )

        response = self._upload(file=arquivo)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "O arquivo ultrapassa o limite máximo de 10 MB.",
        )

    def test_upload_nome_duplicado(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            with patch(
                "core_api.views.document_views.settings.BASE_DIR",
                temp_dir,
            ):
                upload_dir = Path(temp_dir) / "uploads"
                upload_dir.mkdir(parents=True)

                (upload_dir / "teste.pdf").write_bytes(b"arquivo existente")

                arquivo = SimpleUploadedFile(
                    "teste.pdf",
                    b"novo conteudo",
                    content_type="application/pdf",
                )

                response = self._upload(file=arquivo)

                self.assertEqual(response.status_code, 201)

                data = response.json()

                self.assertEqual(
                    data["document"]["saved_name"],
                    "teste_1.pdf",
                )

                self.assertTrue((upload_dir / "teste_1.pdf").exists())
