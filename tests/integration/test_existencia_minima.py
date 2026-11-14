"""
Existência mínima (D11): documento sem permissão devolve só a mensagem de
acesso não autorizado, em cada tela.

Telas: busca da IA, detalhe, visualizador (metadados) e página do visualizador.
O documento "segredo" tem título, código, trecho, área e nível que não podem
aparecer em nenhuma resposta ao usuário sem permissão. Há sempre um controle
(usuário COM permissão vendo as mesmas coisas), para o teste poder falhar se
algo vazar de verdade.
"""

import json
import uuid

from core_api.models import LogAcesso, StatusValidacao
from core_api.services import acesso_negado
from tests.integration.test_aprovar_devolver import _usuario
from tests.integration.test_permissao_busca import PermissaoBase

TITULO = "Plano de ensaio estrutural ZX-9"
CODIGO = "SEC-ZX9-001"
TRECHO = "Carga limite de ruptura da longarina central do prototipo"
ORIGEM = "Laboratorio reservado de estruturas"

BODY_ESPERADO = {
    "detail": "Acesso não autorizado.",
    "codigo": "ACESSO_NAO_AUTORIZADO",
    "pode_solicitar_acesso": True,
}


class ExistenciaMinimaBase(PermissaoBase):
    def setUp(self):
        super().setUp()
        self.segredo = self.documento_com_trecho(
            self.manutencao,
            self.confidencial,
            texto=TRECHO,
            titulo=TITULO,
            identificador_codigo=CODIGO,
            origem_fonte=ORIGEM,
        )
        # `eng` pode ver o segredo; `qual` não tem permissão nenhuma.
        self.conceder(self.eng, self.manutencao, self.confidencial)
        self.vazamentos = [
            TITULO,
            CODIGO,
            TRECHO,
            ORIGEM,
            "Manutenção",
            "CONFIDENCIAL",
            str(self.segredo.id),
        ]

    def get(self, url, token=None):
        return self.client.get(
            url, HTTP_AUTHORIZATION=f"Bearer {token}" if token else ""
        )

    def urls(self, doc):
        return {
            "detalhe": f"/api/documents/{doc.id}/",
            "visualizar": f"/api/documents/{doc.id}/visualizar/",
            "pagina": f"/api/documents/{doc.id}/paginas/1/",
        }

    def sem_vazamento(self, resposta, vazamentos=None):
        texto = resposta.content.decode("utf-8", errors="replace")
        for pedaco in vazamentos or self.vazamentos:
            self.assertNotIn(pedaco, texto)

    def assert_so_a_mensagem(self, resposta):
        self.assertEqual(resposta.status_code, 403, resposta.content)
        self.assertEqual(resposta["Content-Type"], "application/json")
        self.assertEqual(resposta.json(), BODY_ESPERADO)
        self.sem_vazamento(resposta)


class BuscaDaIATests(ExistenciaMinimaBase):
    def test_sem_permissao_nada_do_documento_chega_na_resposta(self):
        resposta, ia = self.perguntar(self.token_qual)
        dados = resposta.json()

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["fontes"], [])
        ia.assert_not_called()  # nem o trecho foi para a IA
        self.sem_vazamento(resposta)

    def test_controle_quem_tem_permissao_recebe_o_documento(self):
        """Prova que o teste de cima poderia falhar: o mesmo documento aparece
        para quem tem permissão."""
        resposta, ia = self.perguntar(self.token_eng)
        fontes = resposta.json()["fontes"]

        self.assertEqual([f["documento_id"] for f in fontes], [str(self.segredo.id)])
        self.assertIn(TRECHO, json.dumps(ia.call_args.args[1], ensure_ascii=False))

    def test_sigilo_acima_do_maximo_tambem_fica_de_fora(self):
        """Tem permissão na área, mas só até INTERNO: o CONFIDENCIAL não vem."""
        perfil, token = _usuario("COMERCIAL")
        self.conceder(perfil, self.manutencao, self.interno)

        resposta, ia = self.perguntar(token)

        self.assertEqual(resposta.json()["fontes"], [])
        ia.assert_not_called()
        self.sem_vazamento(resposta)


class DetalheTests(ExistenciaMinimaBase):
    def test_sem_permissao_so_a_mensagem(self):
        self.assert_so_a_mensagem(
            self.get(self.urls(self.segredo)["detalhe"], self.token_qual)
        )

    def test_controle_com_permissao_ve_o_detalhe(self):
        r = self.get(self.urls(self.segredo)["detalhe"], self.token_eng)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["titulo"], TITULO)

    def test_sem_token_continua_401(self):
        self.assertEqual(self.get(self.urls(self.segredo)["detalhe"]).status_code, 401)

    def test_inexistente_continua_404(self):
        r = self.get(f"/api/documents/{uuid.uuid4()}/", self.token_qual)
        self.assertEqual(r.status_code, 404)
        self.assertNotEqual(r.json(), BODY_ESPERADO)


class VisualizadorTests(ExistenciaMinimaBase):
    def test_metadados_sem_permissao_so_a_mensagem(self):
        r = self.get(self.urls(self.segredo)["visualizar"], self.token_qual)
        self.assert_so_a_mensagem(r)
        self.assertNotIn("titulo", r.json())
        self.assertNotIn("total_paginas", r.json())
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_pagina_sem_permissao_so_a_mensagem_e_nunca_a_imagem(self):
        r = self.get(self.urls(self.segredo)["pagina"], self.token_qual)
        self.assert_so_a_mensagem(r)
        self.assertNotEqual(r["Content-Type"], "image/png")
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_pagina_inexistente_do_documento_proibido_nao_confirma_nada(self):
        """Página fora do intervalo seria 404 para quem pode ver; quem não
        pode ver recebe a mesma mensagem de acesso, sem saber nem o tamanho."""
        r = self.get(f"/api/documents/{self.segredo.id}/paginas/999/", self.token_qual)
        self.assert_so_a_mensagem(r)

    def test_controle_com_permissao_ve_metadados_e_pagina(self):
        urls = self.urls(self.segredo)
        meta = self.get(urls["visualizar"], self.token_eng)
        pagina = self.get(urls["pagina"], self.token_eng)
        self.assertEqual(meta.status_code, 200, meta.content)
        self.assertEqual(meta.json()["titulo"], TITULO)
        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(pagina["Content-Type"], "image/png")

    def test_sem_token_continua_401(self):
        urls = self.urls(self.segredo)
        for url in (urls["visualizar"], urls["pagina"]):
            self.assertEqual(self.get(url).status_code, 401, url)

    def test_inexistente_continua_404(self):
        r = self.get(f"/api/documents/{uuid.uuid4()}/visualizar/", self.token_qual)
        self.assertEqual(r.status_code, 404)
        self.assertNotEqual(r.json(), BODY_ESPERADO)

    def test_negado_nao_grava_log_de_acesso(self):
        urls = self.urls(self.segredo)
        self.get(urls["visualizar"], self.token_qual)
        self.get(urls["pagina"], self.token_qual)
        self.get(urls["detalhe"], self.token_qual)
        self.assertEqual(LogAcesso.objects.filter(documento=self.segredo).count(), 0)


class RespostaIgualParaQualquerMotivoTests(ExistenciaMinimaBase):
    """A resposta não diz POR QUE foi negada: sem permissão na área, sigilo
    acima do máximo e documento ainda em validação dão o mesmo corpo."""

    def corpos(self, doc, token):
        return {
            tela: self.get(url, token).json() for tela, url in self.urls(doc).items()
        }

    def test_mesmo_corpo_em_todas_as_telas_e_motivos(self):
        sem_area = self.segredo  # `qual` não tem permissão em área nenhuma
        sigilo_alto = self.documento_com_trecho(
            self.compliance, self.restrito, titulo="Outro titulo reservado"
        )
        self.conceder(self.qual, self.compliance, self.interno)  # máximo < RESTRITO
        em_validacao = self.documento_com_trecho(
            self.compliance,
            self.publico,
            status=StatusValidacao.AGUARDANDO_VALIDACAO,
            titulo="Ainda em validacao",
        )

        for doc in (sem_area, sigilo_alto, em_validacao):
            for tela, corpo in self.corpos(doc, self.token_qual).items():
                self.assertEqual(corpo, BODY_ESPERADO, (doc.titulo, tela))

    def test_corpo_vem_do_modulo_unico(self):
        self.assertEqual(acesso_negado.corpo(), BODY_ESPERADO)
