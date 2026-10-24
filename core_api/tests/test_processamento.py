"""
Testes do processamento do documento aprovado para a IA (AKA-62 / S2-38).
Um teste por critério de aceite, mais as regras de cada etapa.

Os PDFs são gerados de verdade com o pymupdf, e extração, divisão em trechos,
filtro de qualidade e gravação rodam como em produção. Só a resposta HTTP do
Ollama é simulada (vetores fixos de 1024 dimensões), para o teste não
depender do bge-m3 instalado na máquina.

TransactionTestCase pelo mesmo motivo da S2-33 (triggers deferrable de
documento só disparam no COMMIT) e porque o processamento automático roda
numa thread, com conexão própria, que só enxerga dados já comitados.
"""

import tempfile
import threading
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

import pymupdf
import requests
from django.core.management import CommandError, call_command
from django.db import transaction
from django.test import Client, TransactionTestCase, override_settings

from core_api.management.commands import gerar_embeddings
from core_api.models import (
    Area,
    Categoria,
    Chunk,
    Documento,
    DocumentoArea,
    EmbeddingVersion,
    Idioma,
    NivelSigilo,
    StatusProcessamento,
    StatusValidacao,
    Subcategoria,
    TipoDocumento,
)
from core_api.services import processamento, validacao
from core_api.tests.test_aprovar_devolver import _usuario

ADMIN = "ADMINISTRADOR"
PAGINA_EN = (
    "This specification covers the requirements for the conditioning of "
    "composite test specimens before mechanical testing. The specimens shall "
    "be stored in a controlled environment with a relative humidity of fifty "
    "percent and a temperature of twenty three degrees Celsius for at least "
    "forty hours. The laboratory shall record the conditioning time, the "
    "humidity and the temperature for each batch of specimens. Any deviation "
    "from these conditions shall be reported to the quality department before "
    "the tests are started. Materials that absorb moisture shall be weighed "
    "before and after the conditioning period to confirm that equilibrium has "
    "been reached. "
)
PAGINA_PT = (
    "Este procedimento descreve os requisitos para o condicionamento dos corpos "
    "de prova de material composto antes dos ensaios mecânicos. Os corpos de "
    "prova devem ser armazenados em ambiente controlado, com umidade relativa "
    "de cinquenta por cento e temperatura de vinte e três graus, durante pelo "
    "menos quarenta horas. O laboratório deve registrar o tempo de "
    "condicionamento, a umidade e a temperatura de cada lote. Qualquer desvio "
    "dessas condições deve ser comunicado ao departamento de qualidade antes "
    "do início dos ensaios. "
)
PAGINA_ILEGIVEL = " ".join(["xqzv brtlk mmnop wqrtz zzkvp"] * 40)


def _ollama_falso(url, json=None, timeout=None):
    """Resposta do /api/embed com um vetor de 1024 dimensões por texto."""
    resposta = mock.Mock(status_code=200)
    resposta.json.return_value = {"embeddings": [[0.01] * 1024 for _ in json["input"]]}
    return resposta


def _esperar_processamento(documento_id, timeout=30):
    for thread in threading.enumerate():
        if thread.name == f"processar-documento-{documento_id}":
            thread.join(timeout)
            if thread.is_alive():
                raise AssertionError("o processamento não terminou a tempo")


class ProcessamentoBase(TransactionTestCase):
    # credenciais_db: o detalhe do documento busca os nomes das pessoas lá.
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(DOCUMENTOS_UPLOAD_DIR=self.pasta))
        self.client = Client()
        self.admin, self.token_admin = _usuario(ADMIN)
        self.autor, _ = _usuario("ENGENHARIA")

        self.area = Area.objects.create(nome="Manutenção")
        categoria = Categoria.objects.create(area=self.area, nome="Normas")
        self.subcategoria = Subcategoria.objects.create(
            categoria=categoria, nome="Compatibilidade de Materiais"
        )
        self.tipo = TipoDocumento.objects.create(nome="Manual")
        self.nivel = NivelSigilo.objects.create(nome="INTERNO", ordinal=2)
        self.ingles = Idioma.objects.create(codigo="en", nome="Inglês")
        self.portugues = Idioma.objects.create(codigo="pt", nome="Português")

    # -- fábricas -------------------------------------------------------
    def pdf(self, paginas):
        """Grava um PDF na pasta privada e devolve o nome (como o upload)."""
        nome = f"{uuid.uuid4().hex}.pdf"
        with pymupdf.open() as pdf:
            for texto in paginas:
                pdf.new_page().insert_textbox(
                    pymupdf.Rect(40, 40, 560, 800), texto, fontsize=9
                )
            pdf.save(self.pasta / nome)
        return nome

    def documento(self, paginas=(PAGINA_EN, PAGINA_EN, PAGINA_EN), **extra):
        dados = dict(
            titulo="Conditioning of Composite Specimens",
            identificador_codigo=f"DOC-{uuid.uuid4().hex[:8]}",
            tipo_documento=self.tipo,
            numero_revisao="Rev. A",
            data_emissao="2026-03-10",
            subcategoria=self.subcategoria,
            nivel_sigilo=self.nivel,
            nivel_sigilo_sugerido=self.nivel,
            origem_fonte="Biblioteca técnica",
            responsavel=self.autor,
            idioma=self.ingles,
            arquivo_original_url=self.pdf(paginas) if paginas else "inexistente.pdf",
            usuario_upload=self.autor,
        )
        dados.update(extra)
        with transaction.atomic():
            doc = Documento.objects.create(**dados)
            DocumentoArea.objects.create(
                documento=doc, area=self.area, is_principal=True
            )
        return doc

    def processar(self, doc):
        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=_ollama_falso
        ):
            return processamento.processar_documento(doc.pk)

    def aprovar(self, doc):
        return self.client.post(
            f"/api/documents/{doc.id}/aprovar/",
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token_admin}",
        )

    def assertErro(self, doc, trecho_da_mensagem):
        doc.refresh_from_db()
        self.assertEqual(doc.status_processamento, StatusProcessamento.ERRO)
        self.assertIn(trecho_da_mensagem, doc.erro_processamento)


class ProcessarDocumentoTestCase(ProcessamentoBase):
    def test_documento_vira_trechos_com_embedding_e_pagina(self):
        doc = self.processar(self.documento())
        doc.refresh_from_db()
        self.assertEqual(doc.status_processamento, StatusProcessamento.CONCLUIDO)
        self.assertIsNone(doc.erro_processamento)

        chunks = list(Chunk.objects.filter(documento=doc).order_by("ordem"))
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual([c.ordem for c in chunks], list(range(len(chunks))))
        self.assertEqual(chunks[0].pagina_inicio, 1)
        self.assertEqual(chunks[-1].pagina_fim, 3)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.texto), processamento.TAMANHO_TRECHO)
            self.assertGreaterEqual(chunk.qualidade, processamento.QUALIDADE_MINIMA)
            embedding = EmbeddingVersion.objects.get(chunk=chunk)
            self.assertTrue(embedding.vigente)
            self.assertEqual(embedding.modelo, "bge-m3")
            self.assertEqual(len(embedding.embedding), 1024)

    def test_reprocessar_substitui_os_trechos_em_vez_de_duplicar(self):
        doc = self.documento()
        self.processar(doc)
        primeira = Chunk.objects.filter(documento=doc).count()
        self.processar(doc)
        self.assertEqual(Chunk.objects.filter(documento=doc).count(), primeira)
        self.assertEqual(
            EmbeddingVersion.objects.filter(chunk__documento=doc).count(), primeira
        )

    def test_texto_ilegivel_e_descartado_pelo_filtro_de_qualidade(self):
        doc = self.processar(self.documento(paginas=[PAGINA_ILEGIVEL] * 2))
        self.assertErro(doc, "Nenhum trecho aproveitável")
        self.assertFalse(Chunk.objects.filter(documento=doc).exists())

    def test_pdf_sem_texto_gera_erro(self):
        doc = self.processar(self.documento(paginas=["", ""]))
        self.assertErro(doc, "Nenhum trecho aproveitável")

    def test_filtro_de_qualidade_usa_o_idioma_do_documento(self):
        em_portugues = self.processar(
            self.documento(paginas=[PAGINA_PT] * 3, idioma=self.portugues)
        )
        em_portugues.refresh_from_db()
        self.assertEqual(
            em_portugues.status_processamento, StatusProcessamento.CONCLUIDO
        )
        # O mesmo texto julgado como inglês não passa no filtro.
        marcado_como_ingles = self.processar(self.documento(paginas=[PAGINA_PT] * 3))
        self.assertErro(marcado_como_ingles, "Nenhum trecho aproveitável")

    def test_arquivo_inexistente_gera_erro(self):
        doc = self.processar(self.documento(paginas=None))
        self.assertErro(doc, "Arquivo do documento não encontrado")

    def test_caminho_do_banco_nao_sai_da_pasta_privada(self):
        fora = self.pasta.parent / f"{uuid.uuid4().hex}.pdf"
        fora.write_bytes(b"%PDF-1.7")
        self.addCleanup(fora.unlink)
        doc = self.processar(self.documento(arquivo_original_url=f"../{fora.name}"))
        self.assertErro(doc, "Arquivo do documento não encontrado")

    def test_ollama_fora_do_ar_gera_erro(self):
        doc = self.documento()
        erro = requests.ConnectionError("recusado")
        with mock.patch.object(gerar_embeddings.requests, "post", side_effect=erro):
            processamento.processar_documento(doc.pk)
        self.assertErro(doc, "Não consegui conectar ao Ollama")

    def test_erro_inesperado_tambem_fica_registrado(self):
        doc = self.documento()
        with mock.patch.object(
            processamento.gerar_chunks, "dividir", side_effect=RuntimeError("bug")
        ):
            self.processar(doc)
        self.assertErro(doc, "bug")

    def test_reprocessamento_que_falha_mantem_os_trechos_anteriores(self):
        doc = self.documento()
        self.processar(doc)
        antes = Chunk.objects.filter(documento=doc).count()
        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=requests.ConnectionError
        ):
            processamento.processar_documento(doc.pk)
        self.assertErro(doc, "Ollama")
        self.assertEqual(Chunk.objects.filter(documento=doc).count(), antes)


class SemChamadaExternaTestCase(ProcessamentoBase):
    def test_ollama_fora_da_maquina_local_nao_e_chamado(self):
        doc = self.documento()
        with (
            mock.patch.object(
                gerar_embeddings, "OLLAMA_URL", "http://ollama.empresa.com:11434"
            ),
            mock.patch.object(gerar_embeddings.requests, "post") as post,
        ):
            processamento.processar_documento(doc.pk)
        post.assert_not_called()
        self.assertErro(doc, "não é a máquina local")

    def test_arquivo_em_url_nao_e_baixado(self):
        doc = self.documento(arquivo_original_url="https://storage.exemplo.com/a.pdf")
        with mock.patch.object(gerar_embeddings.requests, "post") as post:
            processamento.processar_documento(doc.pk)
        post.assert_not_called()
        self.assertErro(doc, "sem chamada externa")

    def test_embeddings_vao_so_para_o_ollama_local(self):
        doc = self.documento()
        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=_ollama_falso
        ) as post:
            processamento.processar_documento(doc.pk)
        urls = {chamada.args[0] for chamada in post.call_args_list}
        self.assertEqual(urls, {"http://localhost:11434/api/embed"})


class AprovacaoTestCase(ProcessamentoBase):
    def test_documento_aprovado_vira_trechos_sem_comando_manual(self):
        doc = self.documento()
        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=_ollama_falso
        ):
            self.assertEqual(self.aprovar(doc).status_code, 200)
            _esperar_processamento(doc.pk)
        doc.refresh_from_db()
        self.assertEqual(doc.status_validacao, StatusValidacao.DISPONIVEL)
        self.assertEqual(doc.status_processamento, StatusProcessamento.CONCLUIDO)
        self.assertTrue(EmbeddingVersion.objects.filter(chunk__documento=doc).exists())

    def test_aprovacao_responde_na_hora_sem_esperar_o_processamento(self):
        doc = self.documento()
        liberar, comecou = threading.Event(), threading.Event()

        def processamento_lento(documento_id):
            comecou.set()
            liberar.wait(10)

        with mock.patch.object(
            processamento, "processar_documento", side_effect=processamento_lento
        ) as processar:
            r = self.aprovar(doc)
            self.assertEqual(r.status_code, 200)
            # A resposta chegou com o processamento ainda rodando.
            self.assertTrue(comecou.wait(5))
            self.assertFalse(liberar.is_set())
            liberar.set()
            _esperar_processamento(doc.pk)
        processar.assert_called_once_with(doc.pk)

    def test_processamento_so_comeca_depois_do_commit_da_aprovacao(self):
        doc = self.documento()
        with mock.patch.object(processamento, "_iniciar_thread") as iniciar:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    validacao.aprovar(doc.pk, self.admin, ADMIN)
                    raise RuntimeError("desfaz a aprovação")
        iniciar.assert_not_called()
        doc.refresh_from_db()
        self.assertEqual(doc.status_validacao, StatusValidacao.AGUARDANDO_VALIDACAO)

    def test_devolver_nao_processa(self):
        doc = self.documento()
        with mock.patch.object(processamento, "_iniciar_thread") as iniciar:
            r = self.client.post(
                f"/api/documents/{doc.id}/devolver/",
                data='{"comentario": "Faltou a revisão anterior."}',
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token_admin}",
            )
        self.assertEqual(r.status_code, 200)
        iniciar.assert_not_called()

    @override_settings(PROCESSAMENTO_AUTOMATICO=False)
    def test_processamento_automatico_pode_ser_desligado(self):
        doc = self.documento()
        with mock.patch.object(processamento, "_iniciar_thread") as iniciar:
            self.assertEqual(self.aprovar(doc).status_code, 200)
        iniciar.assert_not_called()

    def test_estado_do_processamento_fica_visivel_no_documento(self):
        doc = self.documento(paginas=None)  # sem arquivo: vai dar erro
        self.assertEqual(self.aprovar(doc).status_code, 200)
        _esperar_processamento(doc.pk)
        r = self.client.get(
            f"/api/documents/{doc.id}/", HTTP_AUTHORIZATION=f"Bearer {self.token_admin}"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status_processamento"], "ERRO")
        self.assertIn("não encontrado", r.json()["erro_processamento"])


class ReprocessarComandoTestCase(ProcessamentoBase):
    def chamar(self, *args):
        saida = StringIO()
        call_command("reprocessar_documento", *args, stdout=saida)
        return saida.getvalue()

    def test_erro_fica_registrado_e_da_para_reprocessar(self):
        doc = self.documento()
        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=requests.ConnectionError
        ):
            processamento.processar_documento(doc.pk)
        self.assertErro(doc, "Ollama")

        with mock.patch.object(
            gerar_embeddings.requests, "post", side_effect=_ollama_falso
        ):
            saida = self.chamar(str(doc.pk))
        doc.refresh_from_db()
        self.assertEqual(doc.status_processamento, StatusProcessamento.CONCLUIDO)
        self.assertIsNone(doc.erro_processamento)
        self.assertIn("Concluído", saida)

    def test_falha_no_comando_mostra_o_motivo(self):
        doc = self.documento(paginas=None)
        with self.assertRaisesMessage(CommandError, "não encontrado"):
            self.chamar(str(doc.pk))

    def test_documento_inexistente(self):
        with self.assertRaisesMessage(CommandError, "não encontrado"):
            self.chamar(str(uuid.uuid4()))

    def test_id_invalido(self):
        with self.assertRaisesMessage(CommandError, "uuid"):
            self.chamar("abc")
