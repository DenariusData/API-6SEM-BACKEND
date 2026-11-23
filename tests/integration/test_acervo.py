"""Acervo de documentos disponíveis ao usuário (AKA-108).

GET /api/documents/disponiveis/ lista os documentos DISPONIVEIS que o usuário
pode consultar, com a mesma regra de permissão da IA, do detalhe e do
visualizador (área + nível de sigilo). Antes não havia como ver o acervo:
só os próprios envios.
"""

from core_api.models import StatusValidacao
from tests.integration.test_permissao_busca import PermissaoBase


class AcervoTests(PermissaoBase):
    def setUp(self):
        super().setUp()
        self.conceder(self.eng, self.manutencao, self.interno)
        self.visivel = self.documento_com_trecho(
            self.manutencao,
            self.publico,
            titulo="Inspeção do trem de pouso",
            identificador_codigo="PROC-001",
        )
        self.sigilo_alto = self.documento_com_trecho(
            self.manutencao, self.confidencial, identificador_codigo="SEC-001"
        )
        self.outra_area = self.documento_com_trecho(
            self.compliance, self.publico, identificador_codigo="CMP-001"
        )
        self.pendente = self.documento_com_trecho(
            self.manutencao,
            self.publico,
            status=StatusValidacao.AGUARDANDO_VALIDACAO,
            identificador_codigo="PEND-001",
        )

    def listar(self, token, busca=None):
        url = "/api/documents/disponiveis/"
        if busca is not None:
            url += f"?busca={busca}"
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")

    def codigos(self, resposta):
        return [d["identificador_codigo"] for d in resposta.json()["results"]]

    def test_lista_so_documentos_disponiveis_com_permissao(self):
        resposta = self.listar(self.token_eng)

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.codigos(resposta), ["PROC-001"])

    def test_nao_mostra_documento_acima_do_sigilo_outra_area_ou_pendente(self):
        texto = self.listar(self.token_eng).content.decode()

        for codigo in ("SEC-001", "CMP-001", "PEND-001"):
            self.assertNotIn(codigo, texto)

    def test_sem_permissao_nenhuma_a_lista_vem_vazia(self):
        resposta = self.listar(self.token_qual)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["results"], [])

    def test_busca_por_titulo_ou_codigo(self):
        self.assertEqual(
            self.codigos(self.listar(self.token_eng, "trem")), ["PROC-001"]
        )
        self.assertEqual(
            self.codigos(self.listar(self.token_eng, "proc-0")), ["PROC-001"]
        )
        self.assertEqual(self.codigos(self.listar(self.token_eng, "inexistente")), [])

    def test_exige_login(self):
        resposta = self.client.get("/api/documents/disponiveis/")

        self.assertEqual(resposta.status_code, 401)
