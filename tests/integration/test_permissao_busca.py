"""
Permissão de acesso a documentos: a busca da IA e o visualizador só entregam
o que o usuário pode ver (S2-26).

Regra: usuário + área + nível de sigilo máximo, sem herança por setor nem por
papel. O documento é acessível se alguma das áreas dele tem permissão para o
usuário e o nível de sigilo dele é menor ou igual ao máximo concedido NAQUELA
área. Tudo na query, antes de qualquer trecho chegar à IA.

Os vetores são fixos (sem Ollama): todo trecho tem a mesma similaridade com a
pergunta, então o que não aparece ficou de fora pela permissão, não por ser
menos parecido.
"""

import json
import uuid
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.utils import timezone

from core_api.management.commands import gerar_embeddings
from core_api.models import (
    Area,
    Chunk,
    Documento,
    DocumentoArea,
    EmbeddingVersion,
    NivelSigilo,
    PerfilOperacional,
    StatusValidacao,
    UsuarioPermissaoArea,
)
from core_api.services.rag import busca_vetorial
from credenciais.models import Papel, Usuario
from tests.integration.test_aprovar_devolver import _usuario
from tests.integration.test_busca_vetorial import EMBED, MODELO, PERGUNTAR_IA, vetor
from tests.integration.test_processamento import ProcessamentoBase


@override_settings(PROCESSAMENTO_AUTOMATICO=False)
class PermissaoBase(ProcessamentoBase):
    def setUp(self):
        super().setUp()
        self.manutencao = self.area  # criada pela base
        self.compliance = Area.objects.create(nome="Compliance")
        self.publico = NivelSigilo.objects.create(nome="PUBLICO", ordinal=1)
        self.interno = self.nivel  # INTERNO, ordinal 2
        self.restrito = NivelSigilo.objects.create(nome="RESTRITO", ordinal=3)
        self.confidencial = NivelSigilo.objects.create(nome="CONFIDENCIAL", ordinal=4)
        self.eng, self.token_eng = _usuario("ENGENHARIA")
        self.qual, self.token_qual = _usuario("QUALIDADE")

    # -- fábricas -------------------------------------------------------
    def conceder(self, usuario, area, nivel):
        return UsuarioPermissaoArea.objects.create(
            usuario=usuario, area=area, nivel_sigilo_maximo=nivel
        )

    def documento_com_trecho(
        self,
        principal,
        nivel,
        secundarias=(),
        status=StatusValidacao.DISPONIVEL,
        texto="Trecho do documento",
        **extra,
    ):
        """Documento nas áreas e no nível pedidos, com um trecho e embedding."""
        if status == StatusValidacao.DISPONIVEL:
            extra.update(validado_por=self.admin, validado_em=timezone.now())
        doc = self.documento(
            nivel_sigilo=nivel,
            nivel_sigilo_sugerido=nivel,
            status_validacao=status,
            **extra,
        )
        with transaction.atomic():
            DocumentoArea.objects.filter(documento=doc).delete()
            DocumentoArea.objects.create(
                documento=doc, area=principal, is_principal=True
            )
            for area in secundarias:
                DocumentoArea.objects.create(
                    documento=doc, area=area, is_principal=False
                )
        chunk = Chunk.objects.create(
            documento=doc, ordem=0, pagina_inicio=1, pagina_fim=1, texto=texto
        )
        EmbeddingVersion.objects.create(chunk=chunk, modelo=MODELO, embedding=vetor())
        return doc

    def buscar(self, usuario):
        usuario_id = getattr(usuario, "pk", usuario)
        return busca_vetorial.buscar_por_vetor(
            vetor(), k=10, modelo=MODELO, usuario_id=usuario_id
        )

    def ids(self, usuario):
        return {t["documento_id"] for t in self.buscar(usuario)}

    def perguntar(self, token):
        """Pergunta pela API (só o Ollama é simulado). Devolve (resposta, ia)."""
        with (
            mock.patch(EMBED, return_value={"embeddings": [vetor()]}),
            mock.patch(PERGUNTAR_IA, return_value="Resposta com base em [1].") as ia,
        ):
            resposta = self.client.post(
                "/api/perguntas/",
                data=json.dumps({"pergunta": "Como condicionar os corpos de prova?"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
        return resposta, ia


class MesmaPerguntaDoisPerfisTests(PermissaoBase):
    def setUp(self):
        super().setUp()
        self.doc_manutencao = self.documento_com_trecho(
            self.manutencao, self.interno, texto="Texto de manutencao"
        )
        self.doc_compliance = self.documento_com_trecho(
            self.compliance, self.interno, texto="Texto de compliance"
        )
        self.conceder(self.eng, self.manutencao, self.interno)
        self.conceder(self.qual, self.compliance, self.interno)

    def test_mesma_pergunta_dois_perfis_fontes_diferentes(self):
        r_eng, _ = self.perguntar(self.token_eng)
        r_qual, _ = self.perguntar(self.token_qual)

        self.assertEqual(r_eng.status_code, 200, r_eng.content)
        self.assertEqual(r_qual.status_code, 200, r_qual.content)
        fontes_eng = {f["documento_id"] for f in r_eng.json()["fontes"]}
        fontes_qual = {f["documento_id"] for f in r_qual.json()["fontes"]}
        self.assertEqual(fontes_eng, {str(self.doc_manutencao.id)})
        self.assertEqual(fontes_qual, {str(self.doc_compliance.id)})

    def test_a_ia_so_recebe_os_trechos_que_o_usuario_pode_ver(self):
        _, ia = self.perguntar(self.token_eng)

        enviados = [t["texto"] for t in ia.call_args.args[1]]
        self.assertEqual(enviados, ["Texto de manutencao"])
        # O texto do documento vedado não está em nenhum lugar do prompt.
        self.assertNotIn("Texto de compliance", json.dumps(ia.call_args.args[1]))

    def test_usuario_sem_permissao_nenhuma_nao_chega_a_chamar_a_ia(self):
        _, token = _usuario("ENGENHARIA")  # perfil novo, sem permissão

        resposta, ia = self.perguntar(token)

        dados = resposta.json()
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertFalse(dados["encontrou"])
        self.assertEqual(dados["fontes"], [])
        ia.assert_not_called()

    def test_revogar_a_permissao_tira_o_documento_da_proxima_pergunta(self):
        self.assertEqual(self.ids(self.eng), {str(self.doc_manutencao.id)})
        UsuarioPermissaoArea.objects.filter(usuario=self.eng).delete()
        self.assertEqual(self.ids(self.eng), set())

    def test_link_do_trecho_abre_o_visualizador_so_para_quem_pode(self):
        (fonte,) = self.perguntar(self.token_eng)[0].json()["fontes"]
        url = f"/api/documents/{fonte['documento_id']}/visualizar/"

        com = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token_eng}")
        sem = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token_qual}")

        self.assertEqual(com.status_code, 200, com.content)
        self.assertEqual(sem.status_code, 404)
        pagina = f"/api/documents/{fonte['documento_id']}/paginas/1/"
        self.assertEqual(
            self.client.get(
                pagina, HTTP_AUTHORIZATION=f"Bearer {self.token_qual}"
            ).status_code,
            404,
        )


class RegraDeAcessoTests(PermissaoBase):
    def test_nivel_do_documento_acima_do_maximo_da_area_fica_de_fora(self):
        doc = self.documento_com_trecho(self.manutencao, self.restrito)
        self.conceder(self.eng, self.manutencao, self.interno)
        self.assertEqual(self.ids(self.eng), set())

        UsuarioPermissaoArea.objects.filter(usuario=self.eng).update(
            nivel_sigilo_maximo=self.restrito
        )
        self.assertEqual(self.ids(self.eng), {str(doc.id)})

    def test_nivel_do_documento_igual_ou_abaixo_do_maximo_entra(self):
        publico = self.documento_com_trecho(self.manutencao, self.publico)
        interno = self.documento_com_trecho(self.manutencao, self.interno)
        restrito = self.documento_com_trecho(self.manutencao, self.restrito)
        self.conceder(self.eng, self.manutencao, self.interno)

        self.assertEqual(self.ids(self.eng), {str(publico.id), str(interno.id)})
        self.assertNotIn(str(restrito.id), self.ids(self.eng))

    def test_o_nivel_maximo_vale_por_area_e_nao_para_todas(self):
        """CONFIDENCIAL em Compliance não abre documento restrito de Manutenção."""
        doc = self.documento_com_trecho(self.manutencao, self.restrito)
        self.conceder(self.eng, self.manutencao, self.publico)
        self.conceder(self.eng, self.compliance, self.confidencial)

        self.assertEqual(self.ids(self.eng), set())
        self.assertNotIn(str(doc.id), self.ids(self.eng))

    def test_permissao_em_outra_area_nao_abre_o_documento(self):
        self.documento_com_trecho(self.manutencao, self.publico)
        self.conceder(self.eng, self.compliance, self.confidencial)
        self.assertEqual(self.ids(self.eng), set())

    def test_basta_uma_das_areas_do_documento(self):
        doc = self.documento_com_trecho(
            self.manutencao, self.interno, secundarias=[self.compliance]
        )
        self.conceder(self.qual, self.compliance, self.interno)  # só a secundária

        self.assertEqual(self.ids(self.qual), {str(doc.id)})

    def test_o_nivel_conta_na_area_que_da_a_permissao(self):
        """Documento em duas áreas: o máximo da área SEM permissão não ajuda."""
        self.documento_com_trecho(
            self.manutencao, self.restrito, secundarias=[self.compliance]
        )
        self.conceder(self.eng, self.manutencao, self.interno)  # abaixo do sigilo
        self.conceder(self.eng, self.compliance, self.publico)  # abaixo do sigilo
        self.assertEqual(self.ids(self.eng), set())

    def test_permissao_nao_libera_documento_que_nao_esta_disponivel(self):
        for status in (
            StatusValidacao.AGUARDANDO_VALIDACAO,
            StatusValidacao.DEVOLVIDO,
        ):
            self.documento_com_trecho(self.manutencao, self.publico, status=status)
        self.conceder(self.eng, self.manutencao, self.confidencial)
        self.assertEqual(self.ids(self.eng), set())

    def test_papel_nao_herda_acesso(self):
        """Administrador sem permissão cadastrada não enxerga nada."""
        self.documento_com_trecho(self.manutencao, self.publico)
        self.assertEqual(self.ids(self.admin), set())

    def test_sem_usuario_ou_usuario_desconhecido_nao_ve_nenhum_documento(self):
        self.documento_com_trecho(self.manutencao, self.publico)
        self.conceder(self.eng, self.manutencao, self.confidencial)

        self.assertEqual(self.ids(None), set())
        self.assertEqual(self.ids(uuid.uuid4()), set())
        self.assertEqual(list(Documento.objects.permitidos_para(None)), [])

    def test_a_permissao_de_um_usuario_nao_vaza_para_o_outro(self):
        doc = self.documento_com_trecho(self.manutencao, self.interno)
        self.conceder(self.eng, self.manutencao, self.interno)
        self.assertEqual(self.ids(self.eng), {str(doc.id)})
        self.assertEqual(self.ids(self.qual), set())

    def test_o_usuario_e_obrigatorio_na_busca(self):
        with self.assertRaises(TypeError):
            busca_vetorial.buscar_por_vetor(vetor(), k=5, modelo=MODELO)

    def test_uma_regra_por_usuario_e_area(self):
        self.conceder(self.eng, self.manutencao, self.interno)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.conceder(self.eng, self.manutencao, self.restrito)


class BasePublicaSemPermissaoTests(PermissaoBase):
    """rag_chunks (EverySpec): norma pública, sem sigilo nem Documento. Segue
    visível para quem não tem permissão nenhuma."""

    def setUp(self):
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute(gerar_embeddings.SQL_CRIAR_TABELA.format(dim=len(vetor())))

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS rag_chunks")
        super().tearDown()

    def test_base_publica_aparece_e_documento_do_sistema_nao(self):
        self.documento_com_trecho(self.manutencao, self.publico)
        texto_vetor = "[" + ",".join(str(x) for x in vetor()) + "]"
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO rag_chunks (id, documento, titulo, url_pdf, "
                "pagina_inicio, pagina_fim, texto, modelo, embedding) "
                "VALUES (%s, 'FED-STD/FED-STD-123H.pdf', 'FED-STD-123H', "
                "'https://everyspec.com/x.pdf', 7, 7, 'Marking shall be', %s, "
                "%s::vector)",
                [uuid.uuid4().hex, MODELO, texto_vetor],
            )

        trechos = self.buscar(self.eng)  # sem nenhuma permissão

        self.assertEqual([t["documento_id"] for t in trechos], [None])


class SeedDasPermissoesDemoTests(PermissaoBase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        super().setUp()
        for codigo in ("ENGENHARIA", "QUALIDADE", "ADMINISTRADOR"):
            Papel.objects.using("credenciais_db").get_or_create(
                codigo=codigo, defaults={"descricao": codigo.title()}
            )

    def rodar(self):
        saida = StringIO()
        call_command("seed_taxonomia", stdout=saida)
        call_command("seed_usuarios_demo", stdout=saida)
        return saida.getvalue()

    def permissoes(self, email):
        usuario = Usuario.objects.using("credenciais_db").get(email=email)
        return {
            (p.area.nome, p.nivel_sigilo_maximo.nome)
            for p in UsuarioPermissaoArea.objects.filter(
                usuario_id=usuario.id
            ).select_related("area", "nivel_sigilo_maximo")
        }

    def test_engenharia_e_qualidade_ficam_com_areas_e_niveis_diferentes(self):
        self.rodar()

        eng = self.permissoes("engenharia@akaer.com.br")
        qual = self.permissoes("qualidade@akaer.com.br")
        self.assertIn(("Manutenção", "RESTRITO"), eng)
        self.assertIn(("Compliance", "CONFIDENCIAL"), qual)
        self.assertNotEqual({a for a, _ in eng}, {a for a, _ in qual})
        self.assertNotIn("Compliance", {a for a, _ in eng})
        self.assertEqual(len(self.permissoes("admin@akaer.com.br")), 4)

    def test_rodar_de_novo_nao_duplica_nem_sobrescreve_ajuste_manual(self):
        self.rodar()
        total = UsuarioPermissaoArea.objects.count()
        manutencao = UsuarioPermissaoArea.objects.get(
            area__nome="Manutenção", nivel_sigilo_maximo__nome="RESTRITO"
        )
        manutencao.nivel_sigilo_maximo = NivelSigilo.objects.get(nome="PUBLICO")
        manutencao.save()

        self.rodar()

        self.assertEqual(UsuarioPermissaoArea.objects.count(), total)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.nivel_sigilo_maximo.nome, "PUBLICO")

    def test_o_perfil_operacional_e_o_mesmo_uuid_do_usuario(self):
        self.rodar()
        ids_perfis = set(PerfilOperacional.objects.values_list("pk", flat=True))
        ids_permissoes = set(
            UsuarioPermissaoArea.objects.values_list("usuario_id", flat=True)
        )
        self.assertTrue(ids_permissoes <= ids_perfis)
