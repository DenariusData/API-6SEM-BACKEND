"""
Testes do reenvio do documento devolvido (S2-36):
PUT /api/documents/<id>/reenviar/. Um teste por critério de aceite, mais a
reutilização da validação do upload e a atomicidade.

Mesma base do teste de upload (TransactionTestCase, dois bancos, pasta de
uploads temporária por teste).
"""

from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from django.test.client import encode_multipart

from core_api.models import (
    AcaoValidacao,
    Categoria,
    Documento,
    DocumentoArea,
    HistoricoValidacao,
    NivelSigilo,
    PerfilOperacional,
    StatusValidacao,
    Subcategoria,
)
from core_api.services import validacao
from core_api.tests import test_upload
from core_api.tests.test_upload import CONTEUDO_PDF

COMENTARIO = "A data de emissão está errada, confira o carimbo."
OUTRO_PDF = CONTEUDO_PDF + b"% versao corrigida\n"


@override_settings(PROCESSAMENTO_AUTOMATICO=False)
class ReenviarBase(test_upload.UploadBase):
    def setUp(self):
        super().setUp()
        self.admin, self.token_admin = self.usuario("ADMINISTRADOR", "Marta Lima")
        self.outro, self.token_outro = self.usuario("ENGENHARIA", "Paulo Dias")
        self.perfil_admin = PerfilOperacional.objects.get(pk=self.admin["id"])
        self.documento = self.devolvido()

    # -- fábricas -------------------------------------------------------
    def devolvido(self, **alteracoes):
        """Documento criado pelo upload real e devolvido pelo administrador."""
        resposta = self.enviar(self.dados(**alteracoes))
        self.assertEqual(resposta.status_code, 201, resposta.content)
        documento = Documento.objects.get(pk=resposta.json()["id"])
        validacao.devolver(documento.pk, self.perfil_admin, "ADMINISTRADOR", COMENTARIO)
        documento.refresh_from_db()
        return documento

    def reenviar(self, dados=None, arquivo=None, token="padrao", id=None):
        dados = dict(dados or {})
        if arquivo is not None:
            dados["arquivo"] = arquivo
        token = self.token_autor if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.put(
            f"/api/documents/{id or self.documento.pk}/reenviar/",
            data=encode_multipart("BoUnDaRy", dados),
            content_type="multipart/form-data; boundary=BoUnDaRy",
            **extra,
        )

    def acoes(self, documento=None):
        documento = documento or self.documento
        return list(
            HistoricoValidacao.objects.filter(documento=documento)
            .order_by("criado_em", "id")
            .values_list("acao", flat=True)
        )

    def recarregar(self):
        self.documento.refresh_from_db()
        return self.documento


class CriteriosDeAceiteTests(ReenviarBase):
    def test_reenviado_volta_para_a_fila(self):
        self.assertEqual(self.documento.status_validacao, StatusValidacao.DEVOLVIDO)

        resposta = self.reenviar({"titulo": "Titulo corrigido"})

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json()["status_validacao"], "AGUARDANDO_VALIDACAO")
        doc = self.recarregar()
        self.assertEqual(doc.status_validacao, StatusValidacao.AGUARDANDO_VALIDACAO)
        self.assertIsNone(doc.comentario_devolucao)
        self.assertEqual(doc.titulo, "Titulo corrigido")
        self.assertEqual(
            self.acoes(),
            [AcaoValidacao.ENVIADO, AcaoValidacao.DEVOLVIDO, AcaoValidacao.REENVIADO],
        )
        # E aparece de fato na fila do validador (S2-35).
        fila = self.client.get(
            "/api/documents/pendentes/",
            HTTP_AUTHORIZATION=f"Bearer {self.token_admin}",
        )
        self.assertEqual(fila.status_code, 200, fila.content)
        self.assertIn(str(doc.pk), fila.content.decode())

    def test_outra_pessoa_recebe_403(self):
        antes = self.recarregar().titulo
        resposta = self.reenviar({"titulo": "Invasor"}, token=self.token_outro)

        self.assertEqual(resposta.status_code, 403, resposta.content)
        doc = self.recarregar()
        self.assertEqual(doc.status_validacao, StatusValidacao.DEVOLVIDO)
        self.assertEqual(doc.titulo, antes)
        self.assertEqual(doc.comentario_devolucao, COMENTARIO)
        self.assertNotIn(AcaoValidacao.REENVIADO, self.acoes())

    def test_administrador_que_nao_e_o_autor_tambem_recebe_403(self):
        resposta = self.reenviar({"titulo": "Admin"}, token=self.token_admin)
        self.assertEqual(resposta.status_code, 403, resposta.content)
        self.assertEqual(self.recarregar().status_validacao, "DEVOLVIDO")

    def test_documento_nao_devolvido_recebe_409(self):
        aguardando = self.documento
        self.reenviar({"titulo": "Primeiro reenvio"})  # agora está aguardando

        resposta = self.reenviar({"titulo": "Segundo reenvio"})

        self.assertEqual(resposta.status_code, 409, resposta.content)
        self.assertEqual(self.recarregar().titulo, "Primeiro reenvio")
        self.assertEqual(aguardando.pk, self.documento.pk)
        self.assertEqual(self.acoes().count(AcaoValidacao.REENVIADO), 1)

    def test_documento_disponivel_recebe_409(self):
        self.reenviar({"titulo": "Corrigido"})
        validacao.aprovar(self.documento.pk, self.perfil_admin, "ADMINISTRADOR")

        resposta = self.reenviar({"titulo": "Depois de aprovado"})

        self.assertEqual(resposta.status_code, 409, resposta.content)
        self.assertEqual(self.recarregar().titulo, "Corrigido")
        self.assertEqual(self.recarregar().status_validacao, "DISPONIVEL")

    def test_arquivo_antigo_e_removido_quando_um_novo_e_enviado(self):
        antigo = self.documento.arquivo_original_url
        self.assertEqual(self.arquivos_na_pasta(), [antigo])

        resposta = self.reenviar(arquivo=self.pdf("v2.pdf", OUTRO_PDF))

        self.assertEqual(resposta.status_code, 200, resposta.content)
        novo = self.recarregar().arquivo_original_url
        self.assertNotEqual(novo, antigo)
        self.assertEqual(self.arquivos_na_pasta(), [novo])
        self.assertEqual((self.pasta / novo).read_bytes(), OUTRO_PDF)

    def test_comentario_anterior_continua_no_historico(self):
        self.reenviar({"titulo": "Corrigido"})

        doc = self.recarregar()
        self.assertIsNone(doc.comentario_devolucao)
        devolucao = HistoricoValidacao.objects.get(
            documento=doc, acao=AcaoValidacao.DEVOLVIDO
        )
        self.assertEqual(devolucao.comentario, COMENTARIO)
        reenvio = HistoricoValidacao.objects.get(
            documento=doc, acao=AcaoValidacao.REENVIADO
        )
        self.assertEqual(reenvio.usuario_id, self.documento.usuario_upload_id)


class ConteudoDoReenvioTests(ReenviarBase):
    def test_so_metadados_mantem_o_arquivo(self):
        arquivo = self.documento.arquivo_original_url

        resposta = self.reenviar({"origem_fonte": "Nova fonte"})

        self.assertEqual(resposta.status_code, 200, resposta.content)
        doc = self.recarregar()
        self.assertEqual(doc.origem_fonte, "Nova fonte")
        self.assertEqual(doc.arquivo_original_url, arquivo)
        self.assertEqual(self.arquivos_na_pasta(), [arquivo])
        # O que não foi enviado não mudou.
        self.assertEqual(doc.titulo, "Compatibility of Materials Guideline")
        self.assertEqual(doc.identificador_codigo, "MAT-COMP-001")

    def test_so_arquivo_mantem_os_metadados(self):
        antes = self.documento.titulo

        resposta = self.reenviar(arquivo=self.pdf("v2.pdf", OUTRO_PDF))

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.recarregar().titulo, antes)
        self.assertEqual(self.recarregar().status_validacao, "AGUARDANDO_VALIDACAO")

    def test_metadados_e_arquivo_juntos(self):
        resposta = self.reenviar(
            {"titulo": "Novo titulo", "numero_revisao": "Rev. C"},
            self.pdf("v2.pdf", OUTRO_PDF),
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        doc = self.recarregar()
        self.assertEqual((doc.titulo, doc.numero_revisao), ("Novo titulo", "Rev. C"))
        self.assertEqual(self.arquivos_na_pasta(), [doc.arquivo_original_url])

    def test_campo_opcional_enviado_vazio_limpa_o_valor(self):
        self.assertEqual(self.documento.referencia_normativa, "AS9100D")
        resposta = self.reenviar({"referencia_normativa": "", "data_validade": ""})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        doc = self.recarregar()
        self.assertIsNone(doc.referencia_normativa)
        self.assertIsNone(doc.data_validade)

    def test_areas_sao_trocadas_juntas(self):
        resposta = self.reenviar(
            {
                "area_principal": self.area_secundaria.id,
                "areas_secundarias": self.area.id,
            }
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        vinculos = {
            (v.area_id, v.is_principal)
            for v in DocumentoArea.objects.filter(documento=self.documento)
        }
        self.assertEqual(
            vinculos, {(self.area_secundaria.id, True), (self.area.id, False)}
        )

    def test_areas_nao_enviadas_continuam(self):
        self.reenviar({"titulo": "So o titulo"})
        vinculos = list(
            DocumentoArea.objects.filter(documento=self.documento).values_list(
                "area_id", "is_principal"
            )
        )
        self.assertEqual(vinculos, [(self.area.id, True)])

    def test_nivel_de_sigilo_atualiza_o_sugerido_tambem(self):
        confidencial = NivelSigilo.objects.create(nome="RESTRITO", ordinal=3)
        resposta = self.reenviar({"nivel_sigilo": confidencial.id})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        doc = self.recarregar()
        self.assertEqual(doc.nivel_sigilo_id, confidencial.id)
        self.assertEqual(doc.nivel_sigilo_sugerido_id, confidencial.id)

    def test_mesmo_identificador_e_revisao_do_proprio_documento_passa(self):
        resposta = self.reenviar(
            {"identificador_codigo": "MAT-COMP-001", "numero_revisao": "Rev. B"}
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)


class ValidacaoReaproveitadaTests(ReenviarBase):
    """Mesmas regras do upload, e requisição recusada não muda nada."""

    def assertNadaMudou(self):
        doc = self.recarregar()
        self.assertEqual(doc.status_validacao, StatusValidacao.DEVOLVIDO)
        self.assertEqual(doc.comentario_devolucao, COMENTARIO)
        self.assertEqual(self.arquivos_na_pasta(), [doc.arquivo_original_url])
        self.assertNotIn(AcaoValidacao.REENVIADO, self.acoes())

    def test_requisicao_sem_campo_nem_arquivo_e_400(self):
        resposta = self.reenviar()
        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertNadaMudou()

    def test_data_em_formato_invalido(self):
        self.assertErroNoCampo(
            self.reenviar({"data_emissao": "10/03/2026"}), "data_emissao"
        )
        self.assertNadaMudou()

    def test_campo_obrigatorio_enviado_vazio(self):
        self.assertErroNoCampo(self.reenviar({"titulo": "  "}), "titulo")
        self.assertNadaMudou()

    def test_tamanho_maximo_do_titulo(self):
        self.assertErroNoCampo(self.reenviar({"titulo": "x" * 301}), "titulo")

    def test_opcao_inexistente(self):
        self.assertErroNoCampo(
            self.reenviar({"tipo_documento": "00000000-0000-0000-0000-000000000000"}),
            "tipo_documento",
        )

    def test_status_invalido(self):
        self.assertErroNoCampo(self.reenviar({"status": "QUALQUER"}), "status")

    def test_subcategoria_de_outra_categoria(self):
        outra = Categoria.objects.create(area=self.area, nome="Outra")
        sub = Subcategoria.objects.create(categoria=outra, nome="Sub de outra")
        resposta = self.reenviar(
            {"categoria": self.categoria.id, "subcategoria": sub.id}
        )
        self.assertErroNoCampo(resposta, "subcategoria")

    def test_area_principal_repetida_nas_secundarias(self):
        resposta = self.reenviar(
            {"area_principal": self.area.id, "areas_secundarias": self.area.id}
        )
        self.assertErroNoCampo(resposta, "areas_secundarias")
        self.assertNadaMudou()

    def test_arquivo_que_nao_e_pdf_e_recusado_sem_tocar_no_antigo(self):
        resposta = self.reenviar(arquivo=self.pdf("falso.pdf", b"nao sou pdf"))
        self.assertErroNoCampo(resposta, "arquivo")
        self.assertNadaMudou()

    def test_arquivo_com_extensao_errada(self):
        resposta = self.reenviar(arquivo=self.pdf("planilha.xlsx", CONTEUDO_PDF))
        self.assertErroNoCampo(resposta, "arquivo")

    def test_arquivo_acima_de_10mb(self):
        grande = CONTEUDO_PDF + b"0" * (10 * 1024 * 1024)
        resposta = self.reenviar(arquivo=self.pdf("grande.pdf", grande))
        self.assertErroNoCampo(resposta, "arquivo")
        self.assertNadaMudou()

    def test_vigencia_anterior_a_emissao_ja_gravada(self):
        # Só a vigência foi enviada: a emissão vem do que está no banco.
        resposta = self.reenviar({"data_vigencia": "2026-01-01"})
        self.assertErroNoCampo(resposta, "data_vigencia")
        self.assertNadaMudou()

    def test_emissao_nova_depois_da_validade_gravada(self):
        resposta = self.reenviar({"data_emissao": "2029-01-01"})
        self.assertErroNoCampo(resposta, "data_vigencia")

    def test_identificador_e_revisao_de_outro_documento_e_409(self):
        outro = self.devolvido(identificador_codigo="OUTRO-1", numero_revisao="Rev. A")
        resposta = self.reenviar(
            {"identificador_codigo": "OUTRO-1", "numero_revisao": "Rev. A"},
            self.pdf("v2.pdf", OUTRO_PDF),
        )
        self.assertErroNoCampo(resposta, "identificador_codigo", status=409)
        doc = self.recarregar()
        self.assertEqual(doc.status_validacao, StatusValidacao.DEVOLVIDO)
        self.assertEqual(doc.identificador_codigo, "MAT-COMP-001")
        self.assertNotIn(AcaoValidacao.REENVIADO, self.acoes())
        # Só os dois arquivos originais; o do reenvio recusado não sobrou.
        self.assertEqual(
            self.arquivos_na_pasta(),
            sorted([doc.arquivo_original_url, outro.arquivo_original_url]),
        )

    def test_corpo_que_nao_e_multipart_e_400(self):
        resposta = self.client.put(
            f"/api/documents/{self.documento.pk}/reenviar/",
            data='{"titulo": "x"}',
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token_autor}",
        )
        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertNadaMudou()


class RotaEFalhasTests(ReenviarBase):
    def test_sem_token_e_401(self):
        resposta = self.reenviar({"titulo": "x"}, token=None)
        self.assertEqual(resposta.status_code, 401)

    def test_documento_inexistente_e_404(self):
        resposta = self.reenviar(
            {"titulo": "x"}, id="00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(resposta.status_code, 404, resposta.content)

    def test_so_aceita_put(self):
        resposta = self.client.post(
            f"/api/documents/{self.documento.pk}/reenviar/",
            HTTP_AUTHORIZATION=f"Bearer {self.token_autor}",
        )
        self.assertEqual(resposta.status_code, 405)

    def test_falha_do_banco_desfaz_tudo_e_nao_deixa_arquivo_orfao(self):
        antigo = self.documento.arquivo_original_url
        with patch.object(
            HistoricoValidacao.objects, "create", side_effect=DatabaseError("boom")
        ):
            resposta = self.reenviar(
                {"titulo": "Nao vai gravar"}, self.pdf("v2.pdf", OUTRO_PDF)
            )

        self.assertEqual(resposta.status_code, 500, resposta.content)
        doc = self.recarregar()
        self.assertEqual(doc.status_validacao, StatusValidacao.DEVOLVIDO)
        self.assertEqual(doc.comentario_devolucao, COMENTARIO)
        self.assertNotEqual(doc.titulo, "Nao vai gravar")
        self.assertEqual(doc.arquivo_original_url, antigo)
        # O arquivo antigo continua lá e o novo não sobrou na pasta.
        self.assertEqual(self.arquivos_na_pasta(), [antigo])

    def test_falha_ao_apagar_o_arquivo_antigo_nao_derruba_o_reenvio(self):
        with patch(
            "core_api.services.reenvio_documento.Path.unlink",
            side_effect=PermissionError("travado"),
        ):
            resposta = self.reenviar(arquivo=self.pdf("v2.pdf", OUTRO_PDF))
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.recarregar().status_validacao, "AGUARDANDO_VALIDACAO")
