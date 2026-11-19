"""
Testes da exportação dos dados pessoais (AKA-75, US-27, LGPD art. 18):
GET /api/auth/me/exportar/?formato=json|pdf. SemDocumentoTests (AKA-77) garante
que nenhum documento vai junto na exportação.

TransactionTestCase e os dois bancos pelo mesmo motivo de test_log_auditoria:
o documento tem triggers deferrable que só disparam no COMMIT, e a identidade
vive em `credenciais_db`.
"""

import json
import unicodedata
import uuid
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pymupdf
from django.db import transaction
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    AcaoValidacao,
    Area,
    Categoria,
    Chunk,
    Documento,
    DocumentoArea,
    HistoricoValidacao,
    Idioma,
    LogAcesso,
    LogAuditoria,
    NivelSigilo,
    PerfilOperacional,
    RegistroPergunta,
    Subcategoria,
    TipoDocumento,
)
from core_api.models.taxonomia import StatusDocumentoChoices
from core_api.services.auth.autenticacao import AuthService
from core_api.utils.jwt_auth import generate_token
from core_api.utils.usuario_atual import com_usuario
from credenciais.models import Usuario

URL = "/api/auth/me/exportar/"
BRASILIA = ZoneInfo("America/Sao_Paulo")
MENSAGEM_LIMITE_DIARIO = (
    "Você atingiu o limite de 5 exportações por dia. Tente novamente amanhã."
)
CAMPOS_PROIBIDOS = [
    "senha_hash",
    "token_confirmacao_hash",
    "token_expira_em",
    "confirmacao_enviada_em",
    "tentativas_login_falhas",
    "tentativas_login_f",
    "bloqueado_ate",
]


@override_settings(PROCESSAMENTO_AUTOMATICO=False)
class ExportacaoBase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        self.ana, self.token_ana = self.usuario("Ana Souza", "ENGENHARIA")
        self.bia, self.token_bia = self.usuario("Bia Campos", "QUALIDADE")
        self.area = Area.objects.create(nome="Manutenção")
        categoria = Categoria.objects.create(area=self.area, nome="Normas")
        self.subcategoria = Subcategoria.objects.create(
            categoria=categoria, nome="Compatibilidade de Materiais"
        )
        self.tipo = TipoDocumento.objects.create(nome="Manual")
        self.nivel = NivelSigilo.objects.create(nome="INTERNO", ordinal=2)
        self.idioma = Idioma.objects.create(codigo="pt", nome="Português")

    def usuario(self, nome, papel):
        dados = AuthService.create_user(
            {
                "email": f"{uuid.uuid4().hex[:8]}@akaer.com.br",
                "password": "x12345",
                "name": nome,
                "role": papel,
            }
        )
        return dados, generate_token(dados)

    def exportar(self, formato="json", token="padrao"):
        token = self.token_ana if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        consulta = {} if formato is None else {"formato": formato}
        return self.client.get(URL, consulta, **extra)

    def exportar_json(self, token="padrao"):
        resposta = self.exportar("json", token)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        return json.loads(resposta.content.decode("utf-8"))

    def depois_de_2_minutos(self):
        """Adianta o relógio em 2 minutos. O log de auditoria é só de inclusão
        (AKA-80), então não dá para recuar o criado_em das exportações feitas."""
        futuro = timezone.now() + timedelta(minutes=2)
        return mock.patch("django.utils.timezone.now", return_value=futuro)

    def subir_documento(self, dono, identificador="MAT-COMP-001", historico=True):
        perfil = PerfilOperacional.objects.get(pk=dono["id"])
        with com_usuario(perfil.pk), transaction.atomic():
            doc = Documento.objects.create(
                titulo="Compatibility of Materials Guideline",
                identificador_codigo=identificador,
                tipo_documento=self.tipo,
                numero_revisao="Rev. B",
                data_emissao="2026-03-10",
                subcategoria=self.subcategoria,
                status=StatusDocumentoChoices.VIGENTE,
                nivel_sigilo=self.nivel,
                nivel_sigilo_sugerido=self.nivel,
                origem_fonte="Biblioteca técnica",
                responsavel=perfil,
                idioma=self.idioma,
                arquivo_original_url="0f3b5c.pdf",
                usuario_upload=perfil,
            )
            DocumentoArea.objects.create(
                documento=doc, area=self.area, is_principal=True
            )
            if historico:
                HistoricoValidacao.objects.create(
                    documento=doc,
                    acao=AcaoValidacao.ENVIADO,
                    usuario=perfil,
                    comentario="Primeira versão.",
                )
        return doc


class ConteudoTests(ExportacaoBase):
    def test_traz_identificacao_e_perfil_do_proprio_usuario(self):
        dados = self.exportar_json()

        ident = dados["identificacao"]
        self.assertEqual(ident["id"], self.ana["id"])
        self.assertEqual(ident["nome"], "Ana Souza")
        self.assertEqual(ident["email"], self.ana["email"])
        self.assertEqual(ident["papel"]["codigo"], "ENGENHARIA")
        self.assertTrue(ident["ativo"])
        self.assertEqual(
            dados["perfil_operacional"]["matricula"], self.ana["matricula"]
        )
        self.assertTrue(dados["nao_incluidos"])

    def test_traz_perguntas_documentos_historico_e_acessos(self):
        doc = self.subir_documento(self.ana)
        LogAcesso.objects.create(
            documento=doc,
            usuario_id=self.ana["id"],
            acao=AcaoAcesso.VISUALIZACAO,
        )
        RegistroPergunta.objects.create(
            usuario_id=self.ana["id"],
            pergunta="Qual o torque do parafuso?",
            encontrou=True,
            quantidade_fontes=2,
            tempo_busca_ms=10,
            tempo_ia_ms=20,
            tempo_total_ms=30,
        )

        dados = self.exportar_json()

        self.assertEqual(
            [p["pergunta"] for p in dados["perguntas"]],
            ["Qual o torque do parafuso?"],
        )
        self.assertNotIn("tempo_total_ms", dados["perguntas"][0])
        [documento] = dados["documentos"]
        self.assertEqual(documento["identificador"], "MAT-COMP-001")
        self.assertEqual(documento["vinculo"], ["ENVIOU", "RESPONSAVEL"])
        [historico] = dados["historico_validacao"]
        self.assertEqual(historico["acao"], "ENVIADO")
        self.assertEqual(historico["comentario"], "Primeira versão.")
        [acesso] = dados["acessos_a_documentos"]
        self.assertEqual(acesso["documento_identificador"], "MAT-COMP-001")
        self.assertNotIn("ip_origem", acesso)
        acoes = {a["acao"] for a in dados["registro_de_atividades"]}
        self.assertIn("CRIADO", acoes)

    def test_acesso_a_documento_apagado_continua_na_exportacao(self):
        # AKA-80: o log sobrevive ao documento (sem FK no banco). A exportação
        # não pode esconder esse acesso; o identificador só fica vazio.
        doc = self.subir_documento(self.ana, "RASCUNHO-001", historico=False)
        LogAcesso.objects.create(
            documento=doc, usuario_id=self.ana["id"], acao=AcaoAcesso.VISUALIZACAO
        )
        doc_id = str(doc.pk)
        Documento.objects.filter(pk=doc.pk).delete()

        [acesso] = self.exportar_json()["acessos_a_documentos"]
        self.assertEqual(acesso["documento_id"], doc_id)
        self.assertIsNone(acesso["documento_identificador"])
        self.assertEqual(acesso["acao"], AcaoAcesso.VISUALIZACAO)

        with self.depois_de_2_minutos():
            resposta = self.exportar("pdf")
        self.assertEqual(resposta.status_code, 200)
        with pymupdf.open("pdf", resposta.content) as pdf:
            texto = "".join(pagina.get_text() for pagina in pdf)
        self.assertIn("VISUALIZACAO", texto)
        self.assertNotIn("IP de origem", texto)

    def test_nao_traz_dados_de_outro_usuario(self):
        self.subir_documento(self.bia, "DOC-DA-BIA")
        RegistroPergunta.objects.create(
            usuario_id=self.bia["id"],
            pergunta="Pergunta da Bia",
            encontrou=False,
            tempo_busca_ms=1,
            tempo_ia_ms=1,
            tempo_total_ms=2,
        )

        conteudo = self.exportar("json").content.decode("utf-8")

        self.assertNotIn(self.bia["id"], conteudo)
        self.assertNotIn(self.bia["email"], conteudo)
        self.assertNotIn("Bia Campos", conteudo)
        self.assertNotIn("DOC-DA-BIA", conteudo)
        self.assertNotIn("Pergunta da Bia", conteudo)

    def test_o_id_vem_do_token_e_nao_da_url(self):
        resposta = self.client.get(
            URL,
            {"formato": "json", "usuario_id": self.bia["id"], "id": self.bia["id"]},
            HTTP_AUTHORIZATION=f"Bearer {self.token_ana}",
        )

        dados = json.loads(resposta.content.decode("utf-8"))
        self.assertEqual(dados["identificacao"]["id"], self.ana["id"])

    def test_nao_exporta_controles_de_seguranca(self):
        usuario = Usuario.objects.using("credenciais_db").get(pk=self.ana["id"])
        usuario.registrar_falha_login(max_tentativas=5, minutos_bloqueio=15)

        conteudo = self.exportar("json").content.decode("utf-8")

        for campo in CAMPOS_PROIBIDOS:
            with self.subTest(campo=campo):
                self.assertNotIn(campo, conteudo)
        self.assertNotIn(usuario.senha_hash, conteudo)


class FormatoTests(ExportacaoBase):
    def test_json_vem_como_anexo_sem_cache(self):
        resposta = self.exportar("json")

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta["Content-Type"].startswith("application/json"))
        self.assertRegex(
            resposta["Content-Disposition"],
            r'^attachment; filename="meus-dados-\d{4}-\d{2}-\d{2}\.json"$',
        )
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_pdf_vem_como_anexo_com_os_mesmos_dados(self):
        resposta = self.exportar("pdf")

        self.assertEqual(resposta.status_code, 200, resposta.content[:200])
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertRegex(resposta["Content-Disposition"], r'\.pdf"$')
        self.assertEqual(resposta["Cache-Control"], "no-store")
        with pymupdf.open("pdf", resposta.content) as doc:
            texto = "".join(pagina.get_text() for pagina in doc)
            texto = unicodedata.normalize("NFKC", texto)
        self.assertIn("Ana Souza", texto)
        self.assertIn(self.ana["email"], texto)
        self.assertIn(self.ana["matricula"], texto)

    def test_formato_invalido_ou_ausente_e_400(self):
        for formato in [None, "", "csv", "xml", "JSONX"]:
            with self.subTest(formato=formato):
                resposta = self.exportar(formato)
                self.assertEqual(resposta.status_code, 400)
                self.assertEqual(resposta.json()["field"], "formato")

    def test_formato_aceita_maiusculas(self):
        self.assertEqual(self.exportar("JSON").status_code, 200)

    def test_sem_token_e_401(self):
        self.assertEqual(self.exportar("json", token=None).status_code, 401)
        self.assertEqual(self.exportar("json", token="invalido").status_code, 401)

    def test_so_aceita_get(self):
        resposta = self.client.post(
            f"{URL}?formato=json", HTTP_AUTHORIZATION=f"Bearer {self.token_ana}"
        )
        self.assertEqual(resposta.status_code, 405)

    def test_usuario_removido_e_404(self):
        Usuario.objects.using("credenciais_db").filter(pk=self.ana["id"]).delete()
        self.assertEqual(self.exportar("json").status_code, 404)


class AuditoriaELimiteTests(ExportacaoBase):
    def test_exportacao_vai_para_o_log_sem_os_dados(self):
        self.exportar("pdf")

        [log] = LogAuditoria.objects.filter(
            acao=AcaoAuditoria.DADOS_PESSOAIS_EXPORTADOS
        )
        self.assertEqual(str(log.usuario_id), self.ana["id"])
        self.assertEqual(log.entidade, "USUARIO")
        self.assertEqual(log.campos_alterados, [])

    def test_no_maximo_uma_exportacao_por_minuto(self):
        self.assertEqual(self.exportar("json").status_code, 200)

        resposta = self.exportar("pdf")

        self.assertEqual(resposta.status_code, 429)
        self.assertGreater(int(resposta["Retry-After"]), 0)
        # A recusada não conta como exportação.
        self.assertEqual(
            LogAuditoria.objects.filter(
                acao=AcaoAuditoria.DADOS_PESSOAIS_EXPORTADOS
            ).count(),
            1,
        )

    def test_limite_e_por_usuario_e_libera_depois_de_1_minuto(self):
        self.assertEqual(self.exportar("json").status_code, 200)
        self.assertEqual(self.exportar("json", self.token_bia).status_code, 200)

        with self.depois_de_2_minutos():
            self.assertEqual(self.exportar("json").status_code, 200)

    def test_formato_invalido_nao_conta_para_o_limite(self):
        self.assertEqual(self.exportar("csv").status_code, 400)
        self.assertEqual(self.exportar("json").status_code, 200)


class SemDocumentoTests(ExportacaoBase):
    """
    AKA-77: a exportação não leva nenhum documento junto. Não vai o arquivo
    nem o conteúdo (texto extraído, trechos/chunks, caminho ou URL do
    arquivo). Só a lista dos documentos ligados ao usuário, com dados de
    cadastro, porque o vínculo da pessoa com o documento é dado dela.
    """

    CAMPOS_DE_CADASTRO = {
        "id",
        "identificador",
        "revisao",
        "titulo",
        "criado_em",
        "validado_em",
        "vinculo",
    }

    def setUp(self):
        super().setUp()
        # Ana envia o DOC-ENVIADO; é só responsável pelo DOC-RESPONSAVEL
        # (enviado pela Bia); só validou o DOC-VALIDADO (da Bia). O
        # DOC-DA-BIA não tem nenhum vínculo com a Ana.
        self.docs = {
            "DOC-ENVIADO": self.documento_com_conteudo(
                "DOC-ENVIADO", upload=self.ana, responsavel=self.bia
            ),
            "DOC-RESPONSAVEL": self.documento_com_conteudo(
                "DOC-RESPONSAVEL", upload=self.bia, responsavel=self.ana
            ),
            "DOC-VALIDADO": self.documento_com_conteudo(
                "DOC-VALIDADO",
                upload=self.bia,
                responsavel=self.bia,
                validador=self.ana,
            ),
            "DOC-DA-BIA": self.documento_com_conteudo(
                "DOC-DA-BIA", upload=self.bia, responsavel=self.bia
            ),
        }

    def documento_com_conteudo(self, ident, upload, responsavel, validador=None):
        """Documento com texto extraído, chunks e caminho do arquivo marcados
        com o identificador, para procurar depois nos arquivos exportados."""
        dono = PerfilOperacional.objects.get(pk=upload["id"])
        with com_usuario(dono.pk), transaction.atomic():
            doc = Documento.objects.create(
                titulo=f"Manual {ident}",
                identificador_codigo=ident,
                tipo_documento=self.tipo,
                numero_revisao="Rev. A",
                data_emissao="2026-03-10",
                subcategoria=self.subcategoria,
                status=StatusDocumentoChoices.VIGENTE,
                nivel_sigilo=self.nivel,
                nivel_sigilo_sugerido=self.nivel,
                origem_fonte="Biblioteca técnica",
                responsavel=PerfilOperacional.objects.get(pk=responsavel["id"]),
                idioma=self.idioma,
                arquivo_original_url=f"uploads/ARQUIVO-{ident}.pdf",
                usuario_upload=dono,
                conteudo=f"TEXTO-EXTRAIDO-{ident}",
            )
            DocumentoArea.objects.create(
                documento=doc, area=self.area, is_principal=True
            )
            for ordem in range(2):
                Chunk.objects.create(
                    documento=doc, ordem=ordem, texto=f"TRECHO-CHUNK-{ident}-{ordem}"
                )
        if validador:
            with com_usuario(validador["id"]), transaction.atomic():
                Documento.objects.filter(pk=doc.pk).update(
                    validado_por_id=validador["id"], validado_em=timezone.now()
                )
        return doc

    def marcas_de_conteudo(self):
        """Tudo o que é conteúdo de documento e não pode sair na exportação."""
        marcas = []
        for doc in Documento.objects.all():
            marcas += [doc.conteudo, doc.arquivo_original_url]
            marcas += [c.texto for c in Chunk.objects.filter(documento=doc)]
        return marcas

    def exportar_pdf(self):
        # O JSON é exportado antes em cada teste: o PDF vem depois do limite
        # de 1 exportação por minuto.
        with self.depois_de_2_minutos():
            resposta = self.exportar("pdf")
        self.assertEqual(resposta.status_code, 200, resposta.content[:200])
        return resposta.content

    def texto_do_pdf(self, conteudo):
        with pymupdf.open("pdf", conteudo) as pdf:
            texto = "".join(pagina.get_text() for pagina in pdf)
        return unicodedata.normalize("NFKC", texto)

    def test_json_e_pdf_nao_trazem_conteudo_dos_documentos(self):
        marcas = self.marcas_de_conteudo()
        # Controle: as marcas existem no banco (16 = 4 docs x texto, caminho
        # e 2 chunks), então a ausência nos arquivos não é falta de dado.
        self.assertEqual(len([m for m in marcas if m]), 16)

        texto_json = self.exportar("json").content.decode("utf-8")
        texto_pdf = self.texto_do_pdf(self.exportar_pdf())

        for formato, texto in (("json", texto_json), ("pdf", texto_pdf)):
            for marca in marcas:
                with self.subTest(formato=formato, marca=marca):
                    self.assertNotIn(marca, texto)
            for campo in ("conteudo", "arquivo_original_url", "chunk", "uploads/"):
                with self.subTest(formato=formato, campo=campo):
                    self.assertNotIn(campo, texto.lower())

    def test_documentos_vem_so_com_dados_de_cadastro(self):
        for doc in self.exportar_json()["documentos"]:
            with self.subTest(documento=doc["identificador"]):
                self.assertEqual(set(doc), self.CAMPOS_DE_CADASTRO)

    def test_pdf_nao_tem_arquivo_anexado_nem_embutido(self):
        self.exportar("json")
        with pymupdf.open("pdf", self.exportar_pdf()) as pdf:
            self.assertEqual(pdf.embfile_count(), 0)
            for pagina in pdf:
                with self.subTest(pagina=pagina.number + 1):
                    anexos = [
                        a
                        for a in pagina.annots()
                        if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT
                    ]
                    self.assertEqual(anexos, [])
                    links = [
                        lk
                        for lk in pagina.get_links()
                        if lk["kind"] in (pymupdf.LINK_URI, pymupdf.LINK_LAUNCH)
                    ]
                    self.assertEqual(links, [])
                    self.assertEqual(pagina.get_images(), [])

    def test_so_aparecem_documentos_ligados_ao_usuario(self):
        documentos = self.exportar_json()["documentos"]

        vinculos = {d["identificador"]: d["vinculo"] for d in documentos}
        self.assertEqual(
            vinculos,
            {
                "DOC-ENVIADO": ["ENVIOU"],
                "DOC-RESPONSAVEL": ["RESPONSAVEL"],
                "DOC-VALIDADO": ["VALIDOU"],
            },
        )
        texto_pdf = self.texto_do_pdf(self.exportar_pdf())
        for ident in ("DOC-ENVIADO", "DOC-RESPONSAVEL", "DOC-VALIDADO"):
            with self.subTest(pdf=ident):
                self.assertIn(ident, texto_pdf)
        self.assertNotIn("DOC-DA-BIA", texto_pdf)
        self.assertNotIn(str(self.docs["DOC-DA-BIA"].pk), texto_pdf)


class LimiteDiarioTests(ExportacaoBase):
    """AKA-76: no máximo 5 exportações por dia (de Brasília) por usuário,
    somando JSON e PDF, além do limite de 1 por minuto.

    O relógio é simulado com mock em `timezone.now` (o mesmo que grava o
    `criado_em` do log), sem editar o log de auditoria. O token JWT usa o
    relógio real, então continua válido.
    """

    def em(self, horario):
        """Executa como se fosse `horario` ("AAAA-MM-DD HH:MM[:SS]") em Brasília."""
        momento = datetime.fromisoformat(horario).replace(tzinfo=BRASILIA)
        return mock.patch("django.utils.timezone.now", return_value=momento)

    def exportar_em(self, horario, formato="json", token="padrao"):
        with self.em(horario):
            return self.exportar(formato, token)

    def exportar_cinco(self, dia, hora=10, token="padrao"):
        """5 exportações (JSON e PDF alternados), 2 minutos uma da outra."""
        for i, formato in enumerate(["json", "pdf", "json", "pdf", "json"]):
            resposta = self.exportar_em(f"{dia} {hora:02d}:{2 * i:02d}", formato, token)
            self.assertEqual(resposta.status_code, 200, resposta.content)

    def exportacoes(self, usuario):
        return LogAuditoria.objects.filter(
            acao=AcaoAuditoria.DADOS_PESSOAIS_EXPORTADOS, usuario_id=usuario["id"]
        ).count()

    def test_quinta_exportacao_e_aceita_e_a_sexta_recusada(self):
        self.exportar_cinco("2026-03-10")

        resposta = self.exportar_em("2026-03-10 10:10", "pdf")

        self.assertEqual(resposta.status_code, 429)
        self.assertEqual(json.loads(resposta.content)["detail"], MENSAGEM_LIMITE_DIARIO)
        # Retry-After até a meia-noite de Brasília: 13 h 50 min a partir das 10:10.
        self.assertIn(int(resposta["Retry-After"]), (49800, 49801))
        # JSON e PDF somam no mesmo limite, e a recusada não conta.
        self.assertEqual(self.exportacoes(self.ana), 5)

    def test_limite_diario_e_por_usuario(self):
        self.exportar_cinco("2026-03-10")

        self.assertEqual(self.exportar_em("2026-03-10 10:20").status_code, 429)
        resposta = self.exportar_em("2026-03-10 10:20", token=self.token_bia)

        self.assertEqual(resposta.status_code, 200)

    def test_libera_no_dia_seguinte_pelo_horario_de_brasilia(self):
        # 21:00 em Brasília já é o dia seguinte em UTC (00:00 UTC): se o dia
        # fosse contado em UTC, as 00:01 de Brasília ainda estariam bloqueadas.
        self.exportar_cinco("2026-03-10", hora=21)

        bloqueada = self.exportar_em("2026-03-10 23:59")
        liberada = self.exportar_em("2026-03-11 00:01")

        self.assertEqual(bloqueada.status_code, 429)
        self.assertEqual(int(bloqueada["Retry-After"]), 61)
        self.assertEqual(liberada.status_code, 200, liberada.content)

    def test_limite_por_minuto_continua_valendo(self):
        self.assertEqual(self.exportar_em("2026-03-10 10:00").status_code, 200)

        resposta = self.exportar_em("2026-03-10 10:00:30", "pdf")

        self.assertEqual(resposta.status_code, 429)
        self.assertIn("segundos", json.loads(resposta.content)["detail"])
        self.assertLessEqual(int(resposta["Retry-After"]), 60)

    def test_no_limite_do_dia_a_mensagem_e_a_do_dia_e_nao_a_do_minuto(self):
        self.exportar_cinco("2026-03-10")

        # 30 s depois da 5ª: os dois limites valem, o do dia é o que importa.
        resposta = self.exportar_em("2026-03-10 10:08:30")

        self.assertEqual(json.loads(resposta.content)["detail"], MENSAGEM_LIMITE_DIARIO)

    def test_formato_invalido_nao_conta_para_o_limite_diario(self):
        for minuto in range(4):
            self.assertEqual(
                self.exportar_em(f"2026-03-10 09:0{minuto}", "csv").status_code, 400
            )

        self.exportar_cinco("2026-03-10")

        self.assertEqual(self.exportacoes(self.ana), 5)
