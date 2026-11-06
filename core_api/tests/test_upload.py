"""
Testes do upload que grava o documento no banco (S2-32) e das rotas de
opções dos selects do formulário. Um teste por critério de aceite, mais as
regras de cada campo.

TransactionTestCase pelo mesmo motivo da S2-33 (triggers deferrable de
documento só disparam no COMMIT) e com os dois bancos, porque usuário e nome
do responsável vivem em `credenciais_db`. Os arquivos vão para uma pasta
temporária por teste (override de DOCUMENTOS_UPLOAD_DIR).
"""

import re
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import Client, TransactionTestCase, override_settings

from core_api.models import (
    AcaoValidacao,
    Area,
    Categoria,
    Documento,
    DocumentoArea,
    HistoricoValidacao,
    Idioma,
    NivelSigilo,
    StatusValidacao,
    Subcategoria,
    TipoDocumento,
)
from core_api.services import upload_documento
from core_api.services.auth.autenticacao import AuthService
from core_api.utils.jwt_auth import generate_token
from credenciais.models import Papel, Usuario

URL = "/api/documents/upload/"
CONTEUDO_PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
CAMPOS_OBRIGATORIOS = [
    "titulo",
    "identificador_codigo",
    "tipo_documento",
    "numero_revisao",
    "data_emissao",
    "area_principal",
    "categoria",
    "subcategoria",
    "status",
    "nivel_sigilo",
    "origem_fonte",
    "responsavel",
    "idioma",
]


class UploadBase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        self.pasta = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(DOCUMENTOS_UPLOAD_DIR=self.pasta))

        self.autor, self.token_autor = self.usuario("ENGENHARIA", "Carlos Eduardo")
        self.responsavel, _ = self.usuario("QUALIDADE", "Ana Souza")

        self.area = Area.objects.create(nome="Manutenção")
        self.area_secundaria = Area.objects.create(nome="Segurança")
        self.categoria = Categoria.objects.create(area=self.area, nome="Normas")
        self.subcategoria = Subcategoria.objects.create(
            categoria=self.categoria, nome="Compatibilidade de Materiais"
        )
        self.tipo = TipoDocumento.objects.create(nome="Manual")
        self.nivel = NivelSigilo.objects.create(nome="INTERNO", ordinal=2)
        self.idioma = Idioma.objects.create(codigo="pt", nome="Português")

    # -- fábricas -------------------------------------------------------
    def usuario(self, role, nome):
        """Usuário real nos dois bancos (credenciais + perfil) e o token dele."""
        dados = AuthService.create_user(
            {
                "email": f"{uuid.uuid4().hex[:8]}@akaer.com.br",
                "password": "x12345",
                "name": nome,
                "role": role,
            }
        )
        return dados, generate_token(dados)

    def dados(self, **alteracoes):
        """Formulário válido. Valor None tira o campo do formulário."""
        dados = {
            "titulo": "Compatibility of Materials Guideline",
            "identificador_codigo": "MAT-COMP-001",
            "tipo_documento": self.tipo.id,
            "numero_revisao": "Rev. B",
            "data_emissao": "2026-03-10",
            "data_vigencia": "2026-04-01",
            "area_principal": self.area.id,
            "categoria": self.categoria.id,
            "subcategoria": self.subcategoria.id,
            "status": "VIGENTE",
            "nivel_sigilo": self.nivel.id,
            "origem_fonte": "Biblioteca técnica",
            "responsavel": self.responsavel["id"],
            "idioma": self.idioma.codigo,
            "referencia_normativa": "AS9100D",
            "data_validade": "2028-03-10",
        }
        dados.update(alteracoes)
        return {campo: valor for campo, valor in dados.items() if valor is not None}

    def pdf(self, nome="guideline.pdf", conteudo=CONTEUDO_PDF):
        return SimpleUploadedFile(nome, conteudo, content_type="application/pdf")

    # -- chamadas -------------------------------------------------------
    def enviar(self, dados=None, arquivo="padrao", token="padrao"):
        dados = self.dados() if dados is None else dict(dados)
        if arquivo == "padrao":
            arquivo = self.pdf()
        if arquivo is not None:
            dados["arquivo"] = arquivo
        token = self.token_autor if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(URL, data=dados, **extra)

    def arquivos_na_pasta(self):
        if not self.pasta.exists():
            return []
        return sorted(p.name for p in self.pasta.iterdir())

    def assertErroNoCampo(self, resposta, campo, status=400):
        self.assertEqual(resposta.status_code, status, resposta.content)
        corpo = resposta.json()
        self.assertEqual(corpo["field"], campo)
        self.assertTrue(corpo["detail"])


class UploadValidoTestCase(UploadBase):
    def test_upload_valido_cria_documento_e_devolve_201_com_id(self):
        r = self.enviar()
        self.assertEqual(r.status_code, 201, r.content)
        doc = Documento.objects.get(pk=r.json()["id"])

        self.assertEqual(doc.titulo, "Compatibility of Materials Guideline")
        self.assertEqual(doc.identificador_codigo, "MAT-COMP-001")
        self.assertEqual(doc.tipo_documento, self.tipo)
        self.assertEqual(doc.numero_revisao, "Rev. B")
        self.assertEqual(str(doc.data_emissao), "2026-03-10")
        self.assertEqual(str(doc.data_vigencia), "2026-04-01")
        self.assertEqual(doc.subcategoria, self.subcategoria)
        self.assertEqual(doc.status, "VIGENTE")
        self.assertEqual(doc.nivel_sigilo_sugerido, self.nivel)
        self.assertEqual(doc.nivel_sigilo, self.nivel)
        self.assertEqual(doc.origem_fonte, "Biblioteca técnica")
        self.assertEqual(str(doc.responsavel_id), self.responsavel["id"])
        self.assertEqual(doc.idioma, self.idioma)
        self.assertEqual(doc.referencia_normativa, "AS9100D")
        self.assertEqual(str(doc.data_validade), "2028-03-10")
        self.assertEqual(
            DocumentoArea.objects.get(documento=doc, is_principal=True).area,
            self.area,
        )

    def test_documento_fica_aguardando_validacao_com_envio_no_historico(self):
        r = self.enviar()
        doc = Documento.objects.get(pk=r.json()["id"])
        self.assertEqual(r.json()["status_validacao"], "AGUARDANDO_VALIDACAO")
        self.assertEqual(doc.status_validacao, StatusValidacao.AGUARDANDO_VALIDACAO)
        historico = list(HistoricoValidacao.objects.filter(documento=doc))
        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0].acao, AcaoValidacao.ENVIADO)
        self.assertEqual(str(historico[0].usuario_id), self.autor["id"])

    def test_usuario_upload_vem_do_token_e_nao_do_formulario(self):
        r = self.enviar(self.dados(usuario_upload=self.responsavel["id"]))
        doc = Documento.objects.get(pk=r.json()["id"])
        self.assertEqual(str(doc.usuario_upload_id), self.autor["id"])

    def test_areas_secundarias_sao_gravadas(self):
        terceira = Area.objects.create(nome="Compliance")
        dados = self.dados(areas_secundarias=[self.area_secundaria.id, terceira.id])
        r = self.enviar(dados)
        self.assertEqual(r.status_code, 201, r.content)
        doc = Documento.objects.get(pk=r.json()["id"])
        secundarias = DocumentoArea.objects.filter(documento=doc, is_principal=False)
        self.assertEqual(
            {a.area_id for a in secundarias}, {self.area_secundaria.id, terceira.id}
        )

    def test_campos_quando_aplicavel_podem_ficar_vazios(self):
        dados = self.dados(
            data_vigencia=None, data_validade="", referencia_normativa=None
        )
        r = self.enviar(dados)
        self.assertEqual(r.status_code, 201, r.content)
        doc = Documento.objects.get(pk=r.json()["id"])
        self.assertIsNone(doc.data_vigencia)
        self.assertIsNone(doc.data_validade)
        self.assertIsNone(doc.referencia_normativa)

    def test_status_aceita_minusculas(self):
        r = self.enviar(self.dados(status="em_revisao"))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Documento.objects.get().status, "EM_REVISAO")

    def test_vigencia_e_validade_no_mesmo_dia_da_emissao_sao_aceitas(self):
        dados = self.dados(data_vigencia="2026-03-10", data_validade="2026-03-10")
        self.assertEqual(self.enviar(dados).status_code, 201)

    def test_documento_recem_enviado_nao_aparece_na_busca(self):
        r = self.enviar()
        self.assertTrue(Documento.objects.filter(pk=r.json()["id"]).exists())
        self.assertFalse(Documento.objects.disponiveis().filter(pk=r.json()["id"]))


class UploadArquivoTestCase(UploadBase):
    def test_arquivo_salvo_com_nome_gerado_sem_url_publica(self):
        r = self.enviar(arquivo=self.pdf("manual-interno.pdf"))
        self.assertEqual(r.status_code, 201, r.content)
        doc = Documento.objects.get(pk=r.json()["id"])

        arquivos = self.arquivos_na_pasta()
        self.assertEqual(len(arquivos), 1)
        self.assertRegex(arquivos[0], r"^[0-9a-f]{32}\.pdf$")
        self.assertEqual(doc.arquivo_original_url, arquivos[0])
        self.assertNotIn("manual-interno", arquivos[0])
        self.assertIsNone(re.search(r"https?://|/", doc.arquivo_original_url))
        self.assertEqual((self.pasta / arquivos[0]).read_bytes(), CONTEUDO_PDF)
        self.assertEqual(
            set(r.json()), {"message", "id", "status_validacao"}
        )  # sem caminho nem URL do arquivo

    def test_arquivo_que_nao_e_pdf_devolve_400(self):
        for nome in ["a.doc", "a.docx", "a.xls", "a.xlsx", "a.txt", "a.png"]:
            with self.subTest(nome=nome):
                r = self.enviar(arquivo=self.pdf(nome))
                self.assertErroNoCampo(r, "arquivo")
        self.assertEqual(Documento.objects.count(), 0)
        self.assertEqual(self.arquivos_na_pasta(), [])

    def test_arquivo_com_extensao_pdf_mas_conteudo_de_outro_tipo_devolve_400(self):
        r = self.enviar(arquivo=self.pdf("falso.pdf", b"PK\x03\x04 docx renomeado"))
        self.assertErroNoCampo(r, "arquivo")

    def test_arquivo_ausente_devolve_400(self):
        self.assertErroNoCampo(self.enviar(arquivo=None), "arquivo")

    def test_arquivo_vazio_devolve_400(self):
        self.assertErroNoCampo(self.enviar(arquivo=self.pdf(conteudo=b"")), "arquivo")

    def test_arquivo_maior_que_10_mb_devolve_400(self):
        grande = CONTEUDO_PDF + b"0" * (10 * 1024 * 1024)
        r = self.enviar(arquivo=self.pdf(conteudo=grande))
        self.assertErroNoCampo(r, "arquivo")

    def test_banco_falhando_nao_deixa_arquivo_orfao(self):
        with patch.object(
            HistoricoValidacao.objects, "create", side_effect=DatabaseError("falhou")
        ):
            r = self.enviar()
        self.assertEqual(r.status_code, 500, r.content)
        self.assertEqual(Documento.objects.count(), 0)
        self.assertEqual(DocumentoArea.objects.count(), 0)
        self.assertEqual(self.arquivos_na_pasta(), [])


class UploadCamposTestCase(UploadBase):
    def test_campo_obrigatorio_faltando_devolve_400_indicando_o_campo(self):
        for campo in CAMPOS_OBRIGATORIOS:
            for vazio in (None, "   "):
                with self.subTest(campo=campo, valor=vazio):
                    r = self.enviar(self.dados(**{campo: vazio}))
                    self.assertErroNoCampo(r, campo)
        self.assertEqual(Documento.objects.count(), 0)
        self.assertEqual(self.arquivos_na_pasta(), [])

    def test_opcao_inexistente_devolve_400(self):
        casos = {
            "tipo_documento": 999999,
            "area_principal": 999999,
            "categoria": "abc",
            "subcategoria": 999999,
            "nivel_sigilo": 999999,
            "responsavel": str(uuid.uuid4()),
            "idioma": "xx",
        }
        for campo, valor in casos.items():
            with self.subTest(campo=campo):
                self.assertErroNoCampo(self.enviar(self.dados(**{campo: valor})), campo)

    def test_responsavel_que_nao_e_uuid_devolve_400(self):
        r = self.enviar(self.dados(responsavel="nao-e-uuid"))
        self.assertErroNoCampo(r, "responsavel")

    def test_responsavel_inativo_devolve_400(self):
        Usuario.objects.using("credenciais_db").filter(
            pk=self.responsavel["id"]
        ).update(ativo=False)
        self.assertErroNoCampo(self.enviar(), "responsavel")

    def test_area_inativa_devolve_400(self):
        Area.objects.filter(pk=self.area.pk).update(ativo=False)
        self.assertErroNoCampo(self.enviar(), "area_principal")

    def test_area_secundaria_inexistente_devolve_400(self):
        r = self.enviar(self.dados(areas_secundarias=[999999]))
        self.assertErroNoCampo(r, "areas_secundarias")

    def test_area_principal_repetida_nas_secundarias_devolve_400(self):
        r = self.enviar(self.dados(areas_secundarias=[self.area.id]))
        self.assertErroNoCampo(r, "areas_secundarias")

    def test_subcategoria_de_outra_categoria_devolve_400(self):
        outra = Categoria.objects.create(area=self.area, nome="Procedimentos")
        r = self.enviar(self.dados(categoria=outra.id))
        self.assertErroNoCampo(r, "subcategoria")

    def test_status_fora_da_lista_devolve_400(self):
        self.assertErroNoCampo(self.enviar(self.dados(status="ATIVO")), "status")

    def test_data_em_formato_invalido_devolve_400(self):
        for campo in ("data_emissao", "data_vigencia", "data_validade"):
            with self.subTest(campo=campo):
                r = self.enviar(self.dados(**{campo: "10/03/2026"}))
                self.assertErroNoCampo(r, campo)

    def test_vigencia_anterior_a_emissao_devolve_400(self):
        r = self.enviar(self.dados(data_vigencia="2026-03-09"))
        self.assertErroNoCampo(r, "data_vigencia")

    def test_validade_anterior_a_emissao_devolve_400(self):
        r = self.enviar(self.dados(data_validade="2025-12-31"))
        self.assertErroNoCampo(r, "data_validade")

    def test_texto_acima_do_limite_devolve_400(self):
        r = self.enviar(self.dados(titulo="x" * 301))
        self.assertErroNoCampo(r, "titulo")


class UploadDuplicadoTestCase(UploadBase):
    def test_identificador_e_revisao_repetidos_devolvem_409(self):
        self.assertEqual(self.enviar().status_code, 201)
        r = self.enviar(self.dados(titulo="Outro título"))
        self.assertErroNoCampo(r, "identificador_codigo", status=409)
        self.assertEqual(Documento.objects.count(), 1)
        self.assertEqual(len(self.arquivos_na_pasta()), 1)

    def test_mesmo_identificador_com_outra_revisao_e_aceito(self):
        self.assertEqual(self.enviar().status_code, 201)
        r = self.enviar(self.dados(numero_revisao="Rev. C"))
        self.assertEqual(r.status_code, 201, r.content)

    def test_envio_simultaneo_barrado_pelo_banco_devolve_409_sem_arquivo_orfao(self):
        self.assertEqual(self.enviar().status_code, 201)
        # Simula a corrida: a checagem prévia não vê o primeiro documento, e
        # quem barra é a constraint única do banco.
        with patch.object(upload_documento, "_ja_existe", side_effect=[False, True]):
            r = self.enviar()
        self.assertErroNoCampo(r, "identificador_codigo", status=409)
        self.assertEqual(Documento.objects.count(), 1)
        self.assertEqual(len(self.arquivos_na_pasta()), 1)


class UploadAutenticacaoTestCase(UploadBase):
    def test_upload_sem_token_devolve_401(self):
        self.assertEqual(self.enviar(token=None).status_code, 401)

    def test_usuario_sem_perfil_operacional_devolve_403(self):
        token = generate_token(
            {"id": str(uuid.uuid4()), "email": "x@akaer.com.br", "role_code": "X"}
        )
        self.assertEqual(self.enviar(token=token).status_code, 403)
        self.assertEqual(self.arquivos_na_pasta(), [])

    def test_get_nao_e_permitido(self):
        r = self.client.get(URL, HTTP_AUTHORIZATION=f"Bearer {self.token_autor}")
        self.assertEqual(r.status_code, 405)


class OpcoesUploadTestCase(UploadBase):
    ROTAS = [
        "/api/tipos-documento/",
        "/api/areas/",
        "/api/niveis-sigilo/",
        "/api/idiomas/",
        "/api/responsaveis/",
    ]

    def get(self, url, token="padrao"):
        token = self.token_autor if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(url, **extra)

    def test_rotas_exigem_autenticacao(self):
        for url in self.ROTAS:
            with self.subTest(url=url):
                self.assertEqual(self.get(url, token=None).status_code, 401)

    def test_tipos_documento(self):
        r = self.get("/api/tipos-documento/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"], [{"id": self.tipo.id, "nome": "Manual"}])

    def test_areas_trazem_categorias_e_subcategorias_ativas(self):
        Area.objects.create(nome="Inativa", ativo=False)
        Categoria.objects.create(area=self.area, nome="Desativada", ativo=False)
        Subcategoria.objects.create(
            categoria=self.categoria, nome="Desativada", ativo=False
        )
        r = self.get("/api/areas/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.json()["results"],
            [
                {
                    "id": self.area.id,
                    "nome": "Manutenção",
                    "categorias": [
                        {
                            "id": self.categoria.id,
                            "nome": "Normas",
                            "subcategorias": [
                                {
                                    "id": self.subcategoria.id,
                                    "nome": "Compatibilidade de Materiais",
                                }
                            ],
                        }
                    ],
                },
                {"id": self.area_secundaria.id, "nome": "Segurança", "categorias": []},
            ],
        )

    def test_niveis_sigilo_em_ordem_de_restricao(self):
        confidencial = NivelSigilo.objects.create(nome="CONFIDENCIAL", ordinal=4)
        publico = NivelSigilo.objects.create(nome="PUBLICO", ordinal=1)
        r = self.get("/api/niveis-sigilo/")
        self.assertEqual(
            [n["id"] for n in r.json()["results"]],
            [publico.id, self.nivel.id, confidencial.id],
        )

    def test_idiomas(self):
        r = self.get("/api/idiomas/")
        self.assertEqual(r.json()["results"], [{"codigo": "pt", "nome": "Português"}])

    def test_responsaveis_trazem_so_id_e_nome(self):
        r = self.get(f"/api/responsaveis/?area={self.area.id}")
        self.assertEqual(r.status_code, 200, r.content)
        resultados = r.json()["results"]
        self.assertEqual(
            resultados,
            [
                {"id": self.responsavel["id"], "nome": "Ana Souza"},
                {"id": self.autor["id"], "nome": "Carlos Eduardo"},
            ],
        )
        self.assertNotIn("@", r.content.decode())

    def test_responsaveis_sem_usuarios_inativos_ou_sem_perfil(self):
        Usuario.objects.using("credenciais_db").filter(
            pk=self.responsavel["id"]
        ).update(ativo=False)
        papel = Papel.objects.using("credenciais_db").get(codigo="ENGENHARIA")
        Usuario.objects.using("credenciais_db").create(
            nome="Sem Perfil", email="semperfil@akaer.com.br", papel=papel
        )
        r = self.get("/api/responsaveis/")
        self.assertEqual([u["nome"] for u in r.json()["results"]], ["Carlos Eduardo"])

    def test_responsaveis_com_area_invalida_devolve_400(self):
        Area.objects.filter(pk=self.area_secundaria.pk).update(ativo=False)
        for area in ("999999", "abc", str(self.area_secundaria.id)):
            with self.subTest(area=area):
                r = self.get(f"/api/responsaveis/?area={area}")
                self.assertErroNoCampo(r, "area")
