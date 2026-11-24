"""
POST /api/documents/sugerir-tipo/: o classificador sugere o tipo do PDF
antes do upload. O Ollama é simulado (gerar_vetores devolve um vetor fixo) e
a rede é um .npz pequeno gravado na pasta temporária de cada teste.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings

from core_api.management.commands.gerar_embeddings import ErroOllama
from core_api.models import TipoDocumento
from core_api.services.auth.autenticacao import AuthService
from core_api.utils.jwt_auth import generate_token

URL = "/api/documents/sugerir-tipo/"
SERVICO = "core_api.services.classificador_tipo"
TEXTO = (
    "This handbook describes the maintenance procedures for the landing gear "
    "of the aircraft, with the inspection steps, tools and torque values. "
) * 3


def vetor_da_classe(indice, dimensao=1024):
    vetor = np.zeros(dimensao)
    vetor[indice] = 1.0
    return vetor.tolist()


class SugerirTipoTest(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.modelo = pasta / "modelo_tipo_documento.npz"
        self.enterContext(override_settings(CLASSIFICADOR_TIPO_MODELO=self.modelo))
        self.enterContext(patch(f"{SERVICO}.validar_ollama_local"))
        self.vetores = self.enterContext(
            patch(
                f"{SERVICO}.gerar_embeddings.gerar_vetores",
                return_value=[vetor_da_classe(0)],
            )
        )
        dados = AuthService.create_user(
            {
                "email": f"{uuid.uuid4().hex[:8]}@akaer.com.br",
                "password": "x12345",
                "name": "Carlos Eduardo",
                "role": "ENGENHARIA",
            }
        )
        self.token = generate_token(dados)
        # O TransactionTestCase esvazia as tabelas: os 4 tipos da migration
        # 0013 são criados de novo aqui.
        for nome in ("Técnica", "Jurídica", "Normativa", "Quantitativa"):
            TipoDocumento.objects.get_or_create(nome=nome)

    # -- fábricas -------------------------------------------------------
    def treinar(self, peso=10.0):
        """Rede de uma camada: a posição i do vetor aponta para a classe i."""
        pesos = np.zeros((1024, 4))
        for i in range(4):
            pesos[i, i] = peso
        np.savez(
            self.modelo,
            W0=pesos,
            b0=np.zeros(4),
            classes=np.array(["Técnica", "Jurídica", "Normativa", "Quantitativa"]),
            modelo_embedding="bge-m3",
        )

    def pdf(self, texto=TEXTO):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_textbox(
                pymupdf.Rect(40, 40, 560, 800), texto, fontsize=9
            )
            conteudo = pdf.tobytes()
        return SimpleUploadedFile("doc.pdf", conteudo, content_type="application/pdf")

    def enviar(self, arquivo="padrao", token="padrao"):
        dados = {}
        if arquivo == "padrao":
            arquivo = self.pdf()
        if arquivo is not None:
            dados["arquivo"] = arquivo
        token = self.token if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(URL, dados, **extra)

    # -- casos ----------------------------------------------------------
    def test_sugere_o_tipo_mais_provavel(self):
        self.treinar()
        tecnica = TipoDocumento.objects.get(nome="Técnica")

        r = self.enviar()

        self.assertEqual(r.status_code, 200)
        sugestao = r.json()["sugestao"]
        self.assertEqual(
            sugestao["tipo_documento"], {"id": tecnica.id, "nome": "Técnica"}
        )
        self.assertGreater(sugestao["confianca"], 0.9)
        self.assertEqual(
            set(sugestao["probabilidades"]),
            {"Técnica", "Jurídica", "Normativa", "Quantitativa"},
        )
        # A rede leu o texto do PDF, com o modelo de embedding salvo no .npz.
        [textos, modelo] = self.vetores.call_args.args
        self.assertIn("landing gear", textos[0])
        self.assertEqual(modelo, "bge-m3")

    def test_em_duvida_nao_sugere_tipo(self):
        self.treinar(peso=0.0)  # as 4 classes empatam em 25%
        sugestao = self.enviar().json()["sugestao"]
        self.assertIsNone(sugestao["tipo_documento"])
        self.assertEqual(sugestao["confianca"], 0.25)

    def test_tipo_inativo_nao_e_sugerido(self):
        self.treinar()
        TipoDocumento.objects.filter(nome="Técnica").update(ativo=False)
        self.assertIsNone(self.enviar().json()["sugestao"]["tipo_documento"])

    def test_sem_modelo_treinado(self):
        r = self.enviar()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["sugestao"])
        self.assertIn("ainda não foi treinado", r.json()["motivo"])
        self.vetores.assert_not_called()

    def test_ia_fora_do_ar(self):
        self.treinar()
        self.vetores.side_effect = ErroOllama("sem conexão")
        r = self.enviar()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["sugestao"])
        self.assertIn("indisponível", r.json()["motivo"])

    def test_pdf_sem_texto(self):
        self.treinar()
        r = self.enviar(arquivo=self.pdf(texto="A"))
        self.assertIsNone(r.json()["sugestao"])
        self.vetores.assert_not_called()

    def test_arquivo_invalido(self):
        for arquivo in (
            None,
            SimpleUploadedFile("doc.txt", b"texto", content_type="text/plain"),
            SimpleUploadedFile("doc.pdf", b"nao e pdf", content_type="application/pdf"),
        ):
            with self.subTest(arquivo=arquivo and arquivo.name):
                r = self.enviar(arquivo=arquivo)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json()["field"], "arquivo")

    def test_exige_login(self):
        self.assertEqual(self.enviar(token=None).status_code, 401)
