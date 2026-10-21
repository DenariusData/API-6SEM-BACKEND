"""
Testes da visualização segura do documento (AKA-48 / S2-43).

Cada classe cobre um critério de aceite. O PDF de teste é gerado na hora
(PyMuPDF) numa pasta temporária que faz o papel de DOCUMENTOS_UPLOAD_DIR.
TransactionTestCase pelo mesmo motivo dos outros testes de documento: os
triggers deferrable só disparam no COMMIT.
"""

import io
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

import pymupdf
from django.test import Client, TransactionTestCase, override_settings
from PIL import Image, ImageChops, ImageFont

from core_api.models import (
    AcaoAcesso,
    Documento,
    LogAcesso,
    PerfilOperacional,
    StatusValidacao,
)
from core_api.services import visualizacao
from core_api.tests.test_documento import _documento_valido
from core_api.utils.jwt_auth import generate_token
from credenciais.models import Papel, Usuario

ADMIN = Papel.ADMINISTRADOR
ENG = Papel.ENGENHARIA


def _pdf(caminho, paginas=2):
    with pymupdf.open() as pdf:
        for n in range(1, paginas + 1):
            pagina = pdf.new_page()
            pagina.insert_text((72, 72), f"Pagina {n} - texto do documento")
        pdf.save(caminho)


def _usuario(role, nome=None):
    """PerfilOperacional (+ Usuario em credenciais_db, se tiver nome) e token."""
    usuario_id = uuid.uuid4()
    email = f"{role.lower()}-{usuario_id.hex[:6]}@akaer.com.br"
    if nome:
        # TransactionTestCase limpa os bancos entre testes, então o papel do
        # seed da migration pode não existir mais.
        Papel.objects.using("credenciais_db").get_or_create(
            codigo=role, defaults={"descricao": role.title()}
        )
        Usuario.objects.using("credenciais_db").create(
            id=usuario_id, nome=nome, email=email, senha_hash="x", papel_id=role
        )
    perfil = PerfilOperacional.objects.create(
        usuario_id=usuario_id, matricula=f"AK-{usuario_id.hex[:8]}"
    )
    token = generate_token({"id": str(usuario_id), "email": email, "role_code": role})
    return perfil, token, email


class VisualizacaoBase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        self.settings_pasta = override_settings(DOCUMENTOS_UPLOAD_DIR=self.pasta)
        self.settings_pasta.enable()
        self.addCleanup(self.settings_pasta.disable)
        visualizacao._total_paginas.cache_clear()
        visualizacao._pagina_sem_marca.cache_clear()

        _pdf(self.pasta / "doc.pdf", paginas=2)
        self.doc, _ = _documento_valido(arquivo_original_url="doc.pdf")
        self.autor = self.doc.usuario_upload
        self.token_autor = generate_token(
            {
                "id": str(self.autor.usuario_id),
                "email": "autor@akaer.com.br",
                "role_code": ENG,
            }
        )
        self.client = Client(REMOTE_ADDR="10.1.2.3")

    def tornar_disponivel(self, doc=None):
        Documento.objects.filter(pk=(doc or self.doc).pk).update(
            status_validacao=StatusValidacao.DISPONIVEL
        )

    def get(self, caminho, token):
        return self.client.get(
            caminho, HTTP_AUTHORIZATION=f"Bearer {token}" if token else ""
        )

    def visualizar(self, token, doc=None):
        return self.get(f"/api/documents/{(doc or self.doc).id}/visualizar/", token)

    def pagina(self, token, n=1, doc=None):
        return self.get(f"/api/documents/{(doc or self.doc).id}/paginas/{n}/", token)


class PaginaChegaComoImagemTestCase(VisualizacaoBase):
    """Página chega como imagem, nunca o arquivo original."""

    def test_visualizar_devolve_total_de_paginas_e_titulo(self):
        r = self.visualizar(self.token_autor)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            r.json(),
            {"total_paginas": 2, "titulo": "Compatibility of Materials Guideline"},
        )

    def test_pagina_e_png_e_nunca_o_pdf(self):
        r = self.pagina(self.token_autor, 1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        self.assertNotIn(b"%PDF", r.content[:1024])
        Image.open(io.BytesIO(r.content)).verify()

    def test_headers_sem_cache_e_inline(self):
        r = self.pagina(self.token_autor, 1)
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        self.assertEqual(self.visualizar(self.token_autor)["Cache-Control"], "no-store")

    def test_pagina_fora_do_intervalo_da_404(self):
        self.assertEqual(self.pagina(self.token_autor, 3).status_code, 404)
        self.assertEqual(self.pagina(self.token_autor, 0).status_code, 404)

    def test_pasta_de_uploads_nao_e_servida_por_nenhuma_rota(self):
        for url in (
            "/uploads/documentos/doc.pdf",
            "/uploads/doc.pdf",
            "/media/doc.pdf",
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)


class PermissaoTestCase(VisualizacaoBase):
    """Sem token recebe 401; sem permissão recebe 404."""

    def test_sem_token_401(self):
        self.assertEqual(self.visualizar(None).status_code, 401)
        self.assertEqual(self.pagina(None).status_code, 401)

    def test_pendente_outro_usuario_comum_404(self):
        _, token, _ = _usuario(ENG)
        self.assertEqual(self.visualizar(token).status_code, 404)
        self.assertEqual(self.pagina(token).status_code, 404)

    def test_pendente_autor_e_validador_podem_ver(self):
        _, token_admin, _ = _usuario(ADMIN)
        self.assertEqual(self.visualizar(self.token_autor).status_code, 200)
        self.assertEqual(self.visualizar(token_admin).status_code, 200)
        self.assertEqual(self.pagina(token_admin).status_code, 200)

    def test_disponivel_qualquer_usuario_logado_ve(self):
        self.tornar_disponivel()
        _, token, _ = _usuario(Papel.QUALIDADE)
        self.assertEqual(self.visualizar(token).status_code, 200)
        self.assertEqual(self.pagina(token).status_code, 200)

    def test_documento_inexistente_404(self):
        r = self.get(f"/api/documents/{uuid.uuid4()}/visualizar/", self.token_autor)
        self.assertEqual(r.status_code, 404)

    def test_arquivo_fora_da_pasta_privada_nao_e_lido(self):
        segredo = self.pasta.parent / f"segredo-{uuid.uuid4().hex}.pdf"
        _pdf(segredo, paginas=1)
        self.addCleanup(segredo.unlink, missing_ok=True)
        Documento.objects.filter(pk=self.doc.pk).update(
            arquivo_original_url=f"../{segredo.name}"
        )
        self.assertEqual(self.visualizar(self.token_autor).status_code, 404)

    def test_url_externa_nao_e_seguida(self):
        Documento.objects.filter(pk=self.doc.pk).update(
            arquivo_original_url="https://exemplo.com/doc.pdf"
        )
        self.assertEqual(self.visualizar(self.token_autor).status_code, 404)


class MarcaDaguaTestCase(VisualizacaoBase):
    """Toda página tem nome, e-mail, data/hora e identificador; cada usuário
    vê o próprio nome; o texto do documento continua legível."""

    def test_texto_da_marca_tem_nome_email_data_hora_e_identificador(self):
        texto = visualizacao.texto_da_marca(
            "Ana Souza", "ana@akaer.com.br", datetime(2026, 10, 19, 21, 5), "MAT-001"
        )
        self.assertEqual(
            texto, "Ana Souza - ana@akaer.com.br - 19/10/2026 21:05 - MAT-001"
        )

    def _texto_usado_por(self, token):
        with mock.patch.object(
            visualizacao, "aplicar_marca_dagua", side_effect=lambda img, txt: img
        ) as marca:
            self.assertEqual(self.pagina(token, 1).status_code, 200)
        return marca.call_args.args[1]

    def test_dois_usuarios_veem_cada_um_o_proprio_nome(self):
        self.tornar_disponivel()
        _, token_ana, email_ana = _usuario(ENG, nome="Ana Souza")
        _, token_rui, email_rui = _usuario(Papel.QUALIDADE, nome="Rui Lima")

        texto_ana = self._texto_usado_por(token_ana)
        texto_rui = self._texto_usado_por(token_rui)

        self.assertIn("Ana Souza", texto_ana)
        self.assertIn(email_ana, texto_ana)
        self.assertNotIn("Rui Lima", texto_ana)
        self.assertIn("Rui Lima", texto_rui)
        self.assertIn(email_rui, texto_rui)
        for texto in (texto_ana, texto_rui):
            self.assertIn(self.doc.identificador_codigo, texto)
            self.assertRegex(texto, r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}")

    def test_nome_e_email_nao_vem_do_front(self):
        self.tornar_disponivel()
        _, token, email = _usuario(ENG, nome="Ana Souza")
        with mock.patch.object(
            visualizacao, "aplicar_marca_dagua", side_effect=lambda img, txt: img
        ) as marca:
            self.client.get(
                f"/api/documents/{self.doc.id}/paginas/1/?nome=Outro&email=x@y",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
        self.assertIn(email, marca.call_args.args[1])
        self.assertNotIn("Outro", marca.call_args.args[1])

    def test_fonte_da_marca_tem_acentos(self):
        # A fonte padrão do Pillow não tem "ã"/"ç" e "João" sairia "Jo□o".
        fonte = visualizacao._fonte()
        padrao_do_pillow = ImageFont.load_default(size=28).getname()[0]
        self.assertNotEqual(fonte.getname()[0], padrao_do_pillow)
        self.assertGreater(fonte.getbbox("ãçé")[2], fonte.getbbox("ace")[2] * 0.8)

    def test_marca_cobre_a_pagina_e_mantem_o_texto_legivel(self):
        original = Image.new("RGB", (600, 800), "white")
        marcada = visualizacao.aplicar_marca_dagua(original, "Ana - ana@x - 01/01/2026")
        self.assertEqual(marcada.size, original.size)

        diferenca = ImageChops.difference(original, marcada).convert("L")
        # a marca aparece nos quatro quadrantes (repetida na página inteira)
        for caixa in (
            (0, 0, 300, 400),
            (300, 0, 600, 400),
            (0, 400, 300, 800),
            (300, 400, 600, 800),
        ):
            self.assertIsNotNone(diferenca.crop(caixa).getbbox(), caixa)
        # ~20% de opacidade: o pixel mais escuro da marca continua claro, então
        # texto preto por baixo segue legível
        self.assertGreater(min(marcada.convert("L").getdata()), 180)

    def test_marca_aplicada_na_resposta_e_cache_so_da_pagina_sem_marca(self):
        sem_marca = visualizacao.pagina_sem_marca(self.pasta / "doc.pdf", 1)
        r = self.pagina(self.token_autor, 1)
        self.assertNotEqual(r.content, sem_marca)
        # o cache guarda só a versão sem marca
        info = visualizacao._pagina_sem_marca.cache_info()
        self.assertGreaterEqual(info.hits, 1)


class LogDeAcessoTestCase(VisualizacaoBase):
    """Toda abertura grava log com usuário, documento, data e IP."""

    def test_visualizar_grava_log_com_ip(self):
        r = self.visualizar(self.token_autor)
        self.assertEqual(r.status_code, 200)
        log = LogAcesso.objects.get(documento=self.doc)
        self.assertEqual(log.usuario, self.autor)
        self.assertEqual(log.acao, AcaoAcesso.VISUALIZACAO)
        self.assertEqual(log.ip_origem, "10.1.2.3")
        self.assertIsNotNone(log.criado_em)

    def test_cada_abertura_grava_um_log(self):
        self.visualizar(self.token_autor)
        self.visualizar(self.token_autor)
        self.assertEqual(LogAcesso.objects.filter(documento=self.doc).count(), 2)

    def test_sem_permissao_nao_grava_log(self):
        _, token, _ = _usuario(ENG)
        self.visualizar(token)
        self.assertFalse(LogAcesso.objects.exists())

    def test_ip_ignora_x_forwarded_for_forjado(self):
        self.client.get(
            f"/api/documents/{self.doc.id}/visualizar/",
            HTTP_AUTHORIZATION=f"Bearer {self.token_autor}",
            HTTP_X_FORWARDED_FOR="1.1.1.1",
        )
        self.assertEqual(LogAcesso.objects.get().ip_origem, "10.1.2.3")


class FormatoTestCase(VisualizacaoBase):
    """Documento que não é PDF devolve 415, sem expor o arquivo."""

    def test_extensao_que_nao_e_pdf_415(self):
        (self.pasta / "antigo.docx").write_bytes(b"PK\x03\x04conteudo")
        Documento.objects.filter(pk=self.doc.pk).update(
            arquivo_original_url="antigo.docx"
        )
        for r in (self.visualizar(self.token_autor), self.pagina(self.token_autor)):
            self.assertEqual(r.status_code, 415)
            self.assertEqual(
                r.json(), {"detail": "Visualização disponível apenas para PDF"}
            )
            self.assertNotIn(b"PK\x03\x04", r.content)

    def test_arquivo_renomeado_para_pdf_415(self):
        (self.pasta / "falso.pdf").write_bytes(b"isto nao e um pdf")
        Documento.objects.filter(pk=self.doc.pk).update(
            arquivo_original_url="falso.pdf"
        )
        self.assertEqual(self.visualizar(self.token_autor).status_code, 415)

    def test_arquivo_sumiu_da_pasta_404(self):
        (self.pasta / "doc.pdf").unlink()
        self.assertEqual(self.visualizar(self.token_autor).status_code, 404)
