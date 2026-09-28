import json
import uuid
from unittest.mock import patch

from django.test import Client, TestCase

from core_api.models import RegistroPergunta
from core_api.services.rag.pergunta import (
    MENSAGEM_NAO_ENCONTROU,
    ErroIA,
    extrair_codigo_e_revisao,
)
from core_api.utils.jwt_auth import generate_token

USUARIO = {
    "id": str(uuid.uuid4()),
    "email": "engenharia@akaer.com.br",
    "role_code": "ENGENHARIA",
}

TRECHOS = [
    {
        "documento": "NASA/CxP_70000_RevB.031898.pdf",
        "titulo": "CxP 70000 (REV. B), CONSTELLATION ARCHITECTURE REQUIREMENTS",
        "url_pdf": "https://everyspec.com/CxP_70000_RevB.pdf",
        "pagina_inicio": 42,
        "pagina_fim": 43,
        "texto": "The Constellation Architecture shall ...",
        "similaridade": 0.62,
    },
    {
        "documento": "FED-STD/FED-STD-123H.020573.pdf",
        "titulo": "FED-STD-123H, FEDERAL STANDARD: MARKING FOR SHIPMENT",
        "url_pdf": "",
        "pagina_inicio": 7,
        "pagina_fim": 7,
        "texto": "Marking shall be ...",
        "similaridade": 0.55,
    },
    {
        "documento": "MIL-HDBK/MIL-HDBK-21.011289.pdf",
        "titulo": "MIL-HDBK-21, MILITARY HANDBOOK: WELDED JOINT DESIGNS",
        "url_pdf": "",
        "pagina_inicio": 1,
        "pagina_fim": 1,
        "texto": "Irrelevante",
        "similaridade": 0.31,
    },
]

BUSCAR = "core_api.services.rag.pergunta.buscar_trechos"
PERGUNTAR_IA = "core_api.services.rag.pergunta.perguntar_ia"


class ExtrairCodigoERevisaoTestCase(TestCase):
    def test_revisao_entre_parenteses(self):
        self.assertEqual(
            extrair_codigo_e_revisao(
                "CxP 70007 (REV. B, CHANGE 003), CONSTELLATION DESIGN", ""
            ),
            ("CxP 70007", "REV. B, CHANGE 003"),
        )

    def test_revisao_como_letra_no_codigo(self):
        self.assertEqual(
            extrair_codigo_e_revisao("MIL-HDBK-17/1F (VOL. 1 OF 5), HANDBOOK", ""),
            ("MIL-HDBK-17/1F", "F"),
        )

    def test_sem_revisao(self):
        self.assertEqual(
            extrair_codigo_e_revisao("MIL-HDBK-21, MILITARY HANDBOOK", ""),
            ("MIL-HDBK-21", None),
        )

    def test_sem_titulo_usa_nome_do_arquivo(self):
        self.assertEqual(
            extrair_codigo_e_revisao("", "NASA/CxP_70057_RevB.031900.pdf"),
            ("CxP_70057_RevB", None),
        )


class PerguntaTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {generate_token(USUARIO)}"}

    def _perguntar(self, pergunta, **headers):
        return self.client.post(
            "/api/perguntas/",
            data=json.dumps({"pergunta": pergunta}),
            content_type="application/json",
            **(headers or self.headers),
        )

    def test_exige_autenticacao(self):
        response = self.client.post(
            "/api/perguntas/",
            data=json.dumps({"pergunta": "teste"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_pergunta_vazia(self):
        response = self._perguntar("   ")
        self.assertEqual(response.status_code, 400)

    @patch(PERGUNTAR_IA, return_value="A arquitetura deve ... [1]")
    @patch(BUSCAR, return_value=TRECHOS)
    def test_resposta_com_fontes(self, mock_buscar, mock_ia):
        response = self._perguntar("Quais os requisitos da arquitetura?")

        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertTrue(dados["encontrou"])
        self.assertEqual(dados["resposta"], "A arquitetura deve ... [1]")

        # O trecho abaixo da similaridade mínima não vai para a IA nem para as fontes
        self.assertEqual(len(mock_ia.call_args.args[1]), 2)
        self.assertEqual(len(dados["fontes"]), 2)

        fonte = dados["fontes"][0]
        self.assertEqual(fonte["documento"], "CxP 70000")
        self.assertEqual(fonte["revisao"], "REV. B")
        self.assertEqual(fonte["pagina"], "42-43")
        self.assertTrue(fonte["citada"])
        self.assertEqual(dados["fontes"][1]["revisao"], "H")
        self.assertFalse(dados["fontes"][1]["citada"])

        self.assertEqual(set(dados["tempos_ms"]), {"busca", "ia", "total"})
        registro = RegistroPergunta.objects.get()
        self.assertTrue(registro.encontrou)
        self.assertEqual(registro.quantidade_fontes, 2)
        self.assertEqual(str(registro.usuario_id), USUARIO["id"])

    @patch(PERGUNTAR_IA)
    @patch(BUSCAR, return_value=TRECHOS[2:])
    def test_pergunta_sem_base_no_acervo(self, mock_buscar, mock_ia):
        response = self._perguntar("Qual a receita de bolo de chocolate?")

        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["resposta"], MENSAGEM_NAO_ENCONTROU)
        self.assertEqual(dados["fontes"], [])
        mock_ia.assert_not_called()
        self.assertFalse(RegistroPergunta.objects.get().encontrou)

    @patch(PERGUNTAR_IA, return_value=MENSAGEM_NAO_ENCONTROU)
    @patch(BUSCAR, return_value=TRECHOS)
    def test_ia_nao_encontra_nos_trechos(self, mock_buscar, mock_ia):
        dados = self._perguntar("Pergunta parecida, mas sem resposta").json()

        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["fontes"], [])

    @patch(BUSCAR, side_effect=ErroIA("Ollama fora do ar"))
    def test_ia_indisponivel(self, mock_buscar):
        with self.assertLogs("core_api.views.pergunta_views", level="ERROR"):
            response = self._perguntar("Qualquer pergunta")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(RegistroPergunta.objects.exists())
