"""
Testes da busca da IA só com documentos disponíveis (AKA-51 / S2-37).
Um teste por critério de aceite, mais a base pública e o documento_id nas
fontes (botão "Ver documento" do visualizador, AKA-61).

Os vetores são fixos (sem Ollama): cada documento recebe um vetor e a
pergunta usa o mesmo vetor, então todos teriam similaridade 1 se não houvesse
filtro. Assim, o que não aparece na resposta ficou de fora pelo filtro, não
por ser menos parecido.

TransactionTestCase pelo mesmo motivo de test_processamento.py: os triggers
deferrable de documento só disparam no COMMIT.
"""

import json
import random
import time
import uuid
from unittest import mock

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core_api.management.commands import gerar_embeddings
from core_api.models import Chunk, Documento, EmbeddingVersion, StatusValidacao
from core_api.services import processamento, validacao
from core_api.services.rag import busca_vetorial, pergunta, responder
from core_api.tests.test_processamento import ProcessamentoBase, _ollama_falso

DIM = EmbeddingVersion.DIMENSAO_ATUAL
MODELO = "bge-m3"
PERGUNTAR_IA = "core_api.services.rag.pergunta.perguntar_ia"
EMBED = "core_api.services.rag.pergunta._ollama"


def vetor(posicao=0):
    """Vetor unitário: dois vetores iguais têm similaridade 1, diferentes 0."""
    v = [0.0] * DIM
    v[posicao] = 1.0
    return v


@override_settings(PROCESSAMENTO_AUTOMATICO=False)
class BuscaBase(ProcessamentoBase):
    def documento_com_trecho(self, status, texto="Trecho do documento", v=None):
        """Documento no estado pedido, já com um trecho e o embedding vigente."""
        extra = {"status_validacao": status}
        if status == StatusValidacao.DISPONIVEL:
            extra.update(validado_por=self.admin, validado_em=timezone.now())
        doc = self.documento(paginas=None, **extra)
        chunk = Chunk.objects.create(
            documento=doc, ordem=0, pagina_inicio=3, pagina_fim=4, texto=texto
        )
        EmbeddingVersion.objects.create(
            chunk=chunk, modelo="bge-m3", embedding=v or vetor()
        )
        return doc

    def ids(self, trechos):
        return {t["documento_id"] for t in trechos}


class CriteriosDeAceiteTests(BuscaBase):
    def test_um_documento_de_cada_estado_so_o_disponivel_aparece(self):
        aguardando = self.documento_com_trecho(StatusValidacao.AGUARDANDO_VALIDACAO)
        devolvido = self.documento_com_trecho(StatusValidacao.DEVOLVIDO)
        disponivel = self.documento_com_trecho(StatusValidacao.DISPONIVEL)

        trechos = busca_vetorial.buscar_por_vetor(vetor(), k=10, modelo=MODELO)

        self.assertEqual(self.ids(trechos), {str(disponivel.id)})
        self.assertNotIn(str(aguardando.id), self.ids(trechos))
        self.assertNotIn(str(devolvido.id), self.ids(trechos))

    def test_aguardando_validacao_nao_aparece_em_nenhuma_resposta(self):
        self.documento_com_trecho(StatusValidacao.AGUARDANDO_VALIDACAO)
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(vetor(), k=10, modelo=MODELO), []
        )

    def test_devolvido_nao_aparece_em_nenhuma_resposta(self):
        self.documento_com_trecho(StatusValidacao.DEVOLVIDO)
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(vetor(), k=10, modelo=MODELO), []
        )

    def test_documento_aprovado_aparece_depois_de_processado(self):
        doc = self.documento()  # aguardando, com PDF de verdade
        consulta = [0.01] * DIM  # mesmo vetor que o Ollama falso devolve

        # Antes de aprovar: não aparece.
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(consulta, k=5, modelo=MODELO), []
        )

        validacao.aprovar(doc.pk, self.admin, "ADMINISTRADOR")
        # Aprovado, mas ainda não processado: ainda não há trecho.
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(consulta, k=5, modelo=MODELO), []
        )

        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=_ollama_falso
        ):
            processamento.processar_documento(doc.pk)

        trechos = busca_vetorial.buscar_por_vetor(consulta, k=5, modelo=MODELO)
        self.assertTrue(trechos)
        self.assertEqual(self.ids(trechos), {str(doc.id)})

    def test_busca_continua_abaixo_de_2_segundos(self):
        """Verificação rápida, não medição: com 500 trechos a busca fica bem
        longe dos 2 s. O número que sustenta o critério é a medição manual com
        20 mil trechos em cada fonte e índice HNSW (~145 ms de mediana, ~220 ms
        no pior caso), descrita em docs/pipeline-rag.md, seção 9."""
        disponivel = self.documento(
            paginas=None,
            status_validacao=StatusValidacao.DISPONIVEL,
            validado_por=self.admin,
            validado_em=timezone.now(),
        )
        chunks = Chunk.objects.bulk_create(
            Chunk(documento=disponivel, ordem=i, texto=f"trecho {i}")
            for i in range(500)
        )
        EmbeddingVersion.objects.bulk_create(
            EmbeddingVersion(chunk=c, modelo="bge-m3", embedding=vetor(i % DIM))
            for i, c in enumerate(chunks)
        )

        inicio = time.perf_counter()
        trechos = busca_vetorial.buscar_por_vetor(vetor(7), k=5, modelo=MODELO)
        duracao = time.perf_counter() - inicio

        self.assertEqual(len(trechos), 5)
        self.assertLess(duracao, 2.0)


class RegraDaBuscaTests(BuscaBase):
    def test_embedding_de_outro_modelo_fica_de_fora(self):
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL)
        EmbeddingVersion.objects.filter(chunk__documento=doc).update(
            modelo="outro-modelo"
        )
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO), []
        )

    def test_embedding_que_nao_e_vigente_fica_de_fora(self):
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL)
        EmbeddingVersion.objects.filter(chunk__documento=doc).update(vigente=False)
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO), []
        )

    def test_ordena_pela_similaridade_e_respeita_k(self):
        perto = self.documento_com_trecho(StatusValidacao.DISPONIVEL, v=vetor(0))
        self.documento_com_trecho(StatusValidacao.DISPONIVEL, v=vetor(1))

        trechos = busca_vetorial.buscar_por_vetor(vetor(0), k=1, modelo=MODELO)

        self.assertEqual(len(trechos), 1)
        self.assertEqual(trechos[0]["documento_id"], str(perto.id))
        self.assertAlmostEqual(trechos[0]["similaridade"], 1.0, places=5)

    def test_trecho_traz_metadados_do_documento(self):
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL, texto="Texto X")
        [t] = busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO)
        self.assertEqual(t["documento"], doc.identificador_codigo)
        self.assertEqual(t["revisao"], doc.numero_revisao or None)
        self.assertEqual(t["titulo"], doc.titulo)
        self.assertEqual((t["pagina_inicio"], t["pagina_fim"]), (3, 4))
        self.assertEqual(t["texto"], "Texto X")
        self.assertIsNone(t["url_pdf"])

    def test_filtro_vem_de_documento_disponiveis(self):
        """Regra num lugar só: o que disponiveis() não devolver, a busca não
        enxerga (é ali que a permissão da Sprint 3 vai entrar)."""
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL)
        with mock.patch.object(
            type(Documento.objects.all()),
            "disponiveis",
            lambda qs: qs.exclude(pk=doc.pk),
        ):
            self.assertEqual(
                busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO), []
            )


class BasePublicaTests(BuscaBase):
    """rag_chunks (EverySpec): norma pública, sem Documento nem validação."""

    def setUp(self):
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute(gerar_embeddings.SQL_CRIAR_TABELA.format(dim=DIM))

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS rag_chunks")
        super().tearDown()

    def publica(self, v, documento="FED-STD/FED-STD-123H.pdf", modelo=MODELO):
        texto_vetor = "[" + ",".join(str(x) for x in v) + "]"
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO rag_chunks (id, documento, titulo, url_pdf, "
                "pagina_inicio, pagina_fim, texto, modelo, embedding) "
                "VALUES (%s, %s, %s, %s, 7, 7, %s, %s, %s::vector)",
                [
                    uuid.uuid4().hex,
                    documento,
                    "FED-STD-123H, FEDERAL STANDARD: MARKING",
                    "https://everyspec.com/FED-STD-123H.pdf",
                    "Marking shall be ...",
                    modelo,
                    texto_vetor,
                ],
            )

    def test_base_publica_entra_sem_documento_id_e_com_link(self):
        self.publica(vetor())
        [t] = busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO)
        self.assertIsNone(t["documento_id"])
        self.assertEqual(t["url_pdf"], "https://everyspec.com/FED-STD-123H.pdf")

    def test_junta_as_duas_fontes_pela_similaridade(self):
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL, v=vetor(0))
        self.publica(vetor(1))
        self.documento_com_trecho(StatusValidacao.DEVOLVIDO, v=vetor(0))

        trechos = busca_vetorial.buscar_por_vetor(vetor(0), k=5, modelo=MODELO)

        self.assertEqual([t["documento_id"] for t in trechos], [str(doc.id), None])

    def test_sem_tabela_da_base_publica_a_busca_continua_e_avisa_no_log(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE rag_chunks")
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL)
        busca_vetorial._avisos_dados.discard("sem_base_publica")

        with self.assertLogs(busca_vetorial.logger, "WARNING") as log:
            trechos = busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO)

        self.assertEqual(self.ids(trechos), {str(doc.id)})
        self.assertIn("rag_chunks", log.output[0])

    def test_base_publica_de_outro_modelo_fica_de_fora(self):
        self.publica(vetor(), modelo="outro-modelo")
        self.assertEqual(
            busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO), []
        )


class FontesNaRespostaTests(BuscaBase):
    """De ponta a ponta pela API: só o Ollama é simulado."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def perguntar(self):
        with (
            mock.patch(EMBED, return_value={"embeddings": [vetor()]}),
            mock.patch(PERGUNTAR_IA, return_value="Resposta com base em [1]."),
        ):
            return self.client.post(
                "/api/perguntas/",
                data=json.dumps({"pergunta": "Como condicionar os corpos de prova?"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token_admin}",
            )

    def test_fonte_do_sistema_traz_documento_id_para_o_visualizador(self):
        doc = self.documento_com_trecho(StatusValidacao.DISPONIVEL)
        self.documento_com_trecho(StatusValidacao.AGUARDANDO_VALIDACAO)
        self.documento_com_trecho(StatusValidacao.DEVOLVIDO)

        resposta = self.perguntar()

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertTrue(dados["encontrou"])
        [fonte] = dados["fontes"]
        self.assertEqual(fonte["documento_id"], str(doc.id))
        self.assertEqual(fonte["documento"], doc.identificador_codigo)
        self.assertEqual(fonte["revisao"], doc.numero_revisao)
        self.assertEqual(fonte["pagina_inicio"], 3)
        self.assertEqual(fonte["pagina"], "3-4")

    def test_sem_documento_disponivel_ia_nao_e_chamada(self):
        self.documento_com_trecho(StatusValidacao.AGUARDANDO_VALIDACAO)
        self.documento_com_trecho(StatusValidacao.DEVOLVIDO)

        with (
            mock.patch(EMBED, return_value={"embeddings": [vetor()]}),
            mock.patch(PERGUNTAR_IA) as ia,
        ):
            resposta = self.client.post(
                "/api/perguntas/",
                data=json.dumps({"pergunta": "Como condicionar os corpos de prova?"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token_admin}",
            )

        dados = resposta.json()
        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["fontes"], [])
        ia.assert_not_called()


class IndiceVetorialTests(BuscaBase):
    """O índice HNSW entrega os vizinhos mais próximos (40 por padrão) e o
    filtro de disponível vem depois. Com muitos trechos de documentos não
    disponíveis mais perto da pergunta, a busca não pode voltar vazia: a busca
    iterativa do pgvector 0.8 continua percorrendo o índice."""

    def test_documento_disponivel_aparece_mesmo_com_muitos_nao_disponiveis_perto(self):
        sorteio = random.Random(51)  # semente fixa: o teste é sempre igual

        def perto_da_pergunta(ruido):
            return [x + (sorteio.random() - 0.5) * ruido for x in vetor(0)]

        pergunta = perto_da_pergunta(0.02)
        devolvido = self.documento(paginas=None, status_validacao="DEVOLVIDO")
        chunks = Chunk.objects.bulk_create(
            Chunk(documento=devolvido, ordem=i, texto=f"trecho {i}") for i in range(300)
        )
        EmbeddingVersion.objects.bulk_create(
            EmbeddingVersion(chunk=c, modelo=MODELO, embedding=perto_da_pergunta(0.06))
            for c in chunks
        )
        # Parecido com a pergunta, mas menos que os 300 do devolvido.
        disponivel = self.documento_com_trecho(
            StatusValidacao.DISPONIVEL, v=perto_da_pergunta(0.12)
        )

        # Mesmo plano da base real (milhares de trechos): percorrer o índice
        # HNSW pela distância e só depois filtrar.
        with connection.cursor() as cursor:
            cursor.execute("REINDEX INDEX embedding_version_hnsw_idx")
            cursor.execute("ANALYZE embedding_version")
            cursor.execute("SET enable_sort = off")
            cursor.execute("SET enable_seqscan = off")
        try:
            trechos = busca_vetorial.buscar_por_vetor(pergunta, k=5, modelo=MODELO)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET enable_sort")
                cursor.execute("RESET enable_seqscan")

        self.assertEqual(self.ids(trechos), {str(disponivel.id)})


class PaginasSemNumeroTests(TestCase):
    """Trecho do sistema pode vir sem página (pagina_inicio nula)."""

    def test_scripts_e_api_nao_mostram_none(self):
        self.assertEqual(responder.formatar_paginas(None, None), "p. —")
        self.assertEqual(responder.formatar_paginas(3, None), "p. 3")
        self.assertEqual(pergunta.formatar_paginas(None, None), "—")
