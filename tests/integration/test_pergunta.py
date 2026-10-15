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
from core_api.services.rag.responder import (
    FRASES_NAO_ENCONTROU,
    montar_prompt_sistema,
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

    def _perguntar(self, pergunta, idioma=None, **headers):
        data = {"pergunta": pergunta}
        if idioma is not None:
            data["idioma"] = idioma
        return self.client.post(
            "/api/perguntas/",
            data=json.dumps(data),
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
        self.assertEqual(dados["idioma"], "pt-BR")
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
        self.assertEqual(registro.idioma, "pt-BR")
        self.assertTrue(registro.encontrou)
        self.assertEqual(registro.quantidade_fontes, 2)
        self.assertEqual(str(registro.usuario_id), USUARIO["id"])

    @patch(PERGUNTAR_IA, return_value="The architecture must ... [1]")
    @patch(BUSCAR, return_value=TRECHOS)
    def test_resposta_em_ingles(self, mock_buscar, mock_ia):
        response = self._perguntar(
            "What are the architecture requirements?", idioma="en"
        )

        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertTrue(dados["encontrou"])
        self.assertEqual(dados["idioma"], "en")
        self.assertEqual(dados["resposta"], "The architecture must ... [1]")
        self.assertEqual(len(dados["fontes"]), 2)

        registro = RegistroPergunta.objects.get()
        self.assertEqual(registro.idioma, "en")
        self.assertTrue(registro.encontrou)
        self.assertEqual(mock_ia.call_args.kwargs.get("idioma"), "en")

    @patch(PERGUNTAR_IA)
    @patch(BUSCAR, return_value=TRECHOS)
    def test_mesma_pergunta_respondida_em_pt_e_en_com_fontes(
        self, mock_buscar, mock_ia
    ):
        mock_ia.side_effect = [
            "A arquitetura requer ... [1]",
            "The architecture requires ... [1]",
        ]

        pergunta = "Quais os requisitos da arquitetura?"

        # 1. Pergunta respondida em PT citando fonte
        resp_pt = self._perguntar(pergunta, idioma="pt-BR")
        self.assertEqual(resp_pt.status_code, 200)
        dados_pt = resp_pt.json()
        self.assertEqual(dados_pt["idioma"], "pt-BR")
        self.assertEqual(dados_pt["resposta"], "A arquitetura requer ... [1]")
        self.assertTrue(dados_pt["encontrou"])
        self.assertEqual(len(dados_pt["fontes"]), 2)
        self.assertTrue(dados_pt["fontes"][0]["citada"])

        # 2. Mesma pergunta respondida em EN citando fonte
        resp_en = self._perguntar(pergunta, idioma="en")
        self.assertEqual(resp_en.status_code, 200)
        dados_en = resp_en.json()
        self.assertEqual(dados_en["idioma"], "en")
        self.assertEqual(dados_en["resposta"], "The architecture requires ... [1]")
        self.assertTrue(dados_en["encontrou"])
        self.assertEqual(len(dados_en["fontes"]), 2)
        self.assertTrue(dados_en["fontes"][0]["citada"])

        registros = list(RegistroPergunta.objects.order_by("criado_em"))
        self.assertEqual(len(registros), 2)
        self.assertEqual(registros[0].idioma, "pt-BR")
        self.assertEqual(registros[1].idioma, "en")

    @patch(PERGUNTAR_IA, return_value="The architecture requires ... [1]")
    @patch(BUSCAR, return_value=TRECHOS)
    def test_trecho_citado_continua_idioma_original(self, mock_buscar, mock_ia):
        response = self._perguntar(
            "What are the architecture requirements?", idioma="en"
        )
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        # Trechos e títulos dos documentos originais não são traduzidos
        self.assertIn(
            "CONSTELLATION ARCHITECTURE REQUIREMENTS", dados["fontes"][0]["titulo"]
        )

    def test_idioma_invalido_devolve_400(self):
        for idioma_invalido in ["es", "fr", "pt", "invalid", ""]:
            response = self._perguntar("Qualquer pergunta", idioma=idioma_invalido)
            self.assertEqual(response.status_code, 400)
            dados = response.json()
            self.assertIn("Idioma inválido", dados["detail"])
        self.assertFalse(RegistroPergunta.objects.exists())

    @patch(PERGUNTAR_IA, return_value="Resposta padrão ... [1]")
    @patch(BUSCAR, return_value=TRECHOS)
    def test_sem_idioma_usa_padrao_pt_br(self, mock_buscar, mock_ia):
        response = self._perguntar("Pergunta sem campo idioma")
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertEqual(dados["idioma"], "pt-BR")
        registro = RegistroPergunta.objects.get()
        self.assertEqual(registro.idioma, "pt-BR")

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

    @patch(PERGUNTAR_IA)
    @patch(BUSCAR, return_value=TRECHOS[2:])
    def test_pergunta_sem_base_no_acervo_em_ingles(self, mock_buscar, mock_ia):
        response = self._perguntar("How to bake a chocolate cake?", idioma="en")

        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["idioma"], "en")
        self.assertEqual(dados["resposta"], FRASES_NAO_ENCONTROU["en"])
        self.assertEqual(dados["fontes"], [])
        mock_ia.assert_not_called()
        registro = RegistroPergunta.objects.get()
        self.assertEqual(registro.idioma, "en")
        self.assertFalse(registro.encontrou)

    @patch(PERGUNTAR_IA, return_value=MENSAGEM_NAO_ENCONTROU)
    @patch(BUSCAR, return_value=TRECHOS)
    def test_ia_nao_encontra_nos_trechos(self, mock_buscar, mock_ia):
        dados = self._perguntar("Pergunta parecida, mas sem resposta").json()

        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["fontes"], [])

    @patch(PERGUNTAR_IA, return_value=FRASES_NAO_ENCONTROU["en"])
    @patch(BUSCAR, return_value=TRECHOS)
    def test_ia_nao_encontra_nos_trechos_em_ingles(self, mock_buscar, mock_ia):
        dados = self._perguntar("Similar question without answer", idioma="en").json()

        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["idioma"], "en")
        self.assertEqual(dados["fontes"], [])

    @patch(BUSCAR, side_effect=ErroIA("Ollama fora do ar"))
    def test_ia_indisponivel(self, mock_buscar):
        with self.assertLogs("core_api.views.pergunta_views", level="ERROR"):
            response = self._perguntar("Qualquer pergunta")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(RegistroPergunta.objects.exists())


class PromptSistemaTestCase(TestCase):
    def test_prompt_pt_br(self):
        prompt = montar_prompt_sistema("pt-BR")
        self.assertIn("Responda sempre em português do Brasil", prompt)
        self.assertIn(
            "Não encontrei essa informação nos documentos consultados.", prompt
        )
        self.assertIn(
            "Indique a fonte de cada informação com o número do trecho entre colchetes",
            prompt,
        )
        self.assertIn(
            "Mantenha siglas, códigos e números exatamente como no original", prompt
        )
        self.assertIn(
            "Trechos citados devem permanecer no idioma original do documento", prompt
        )

    def test_prompt_en(self):
        prompt = montar_prompt_sistema("en")
        self.assertIn("Answer always in English", prompt)
        self.assertIn(
            "I did not find this information in the consulted documents.", prompt
        )
        self.assertIn(
            (
                "Indicate the source of each information with the excerpt number"
                " in brackets"
            ),
            prompt,
        )
        self.assertIn("for example [1] or [2]", prompt)
        self.assertNotIn("ou [2]", prompt)
        self.assertIn(
            "Keep acronyms, codes and numbers exactly as in the original", prompt
        )
        self.assertIn(
            "Cited excerpts must remain in the original language of the document",
            prompt,
        )
