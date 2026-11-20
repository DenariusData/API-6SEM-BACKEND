"""
Validação de demonstração do filtro de permissão (S2-26): o comando
`validar_permissao_demo` e o serviço que o sustenta.

O Ollama é simulado (como nos outros testes de busca) e todos os trechos têm a
mesma similaridade com a pergunta: o que não aparece nas fontes ficou de fora
pela permissão, não por ser menos parecido. O que se prova aqui é que a
validação APONTA o problema quando o filtro falha, não só que passa quando
tudo está certo.
"""

import json
import tempfile
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from core_api.models import Documento, PerfilOperacional, StatusValidacao
from core_api.services import validacao_permissao as validacao
from core_api.services.rag import busca_vetorial
from credenciais.models import Papel, Usuario
from tests.integration.test_busca_vetorial import EMBED, PERGUNTAR_IA, vetor
from tests.integration.test_permissao_busca import PermissaoBase

PERGUNTA = "Como condicionar os corpos de prova?"
BUSCA = "core_api.services.rag.pergunta.buscar_por_vetor"


def _doc(areas, sigilo_ordinal, codigo="DOC-1"):
    return {
        "id": codigo,
        "codigo": codigo,
        "sigilo": f"N{sigilo_ordinal}",
        "ordinal": sigilo_ordinal,
        "areas": [
            {"id": a, "nome": f"Área {a}", "principal": i == 0}
            for i, a in enumerate(areas)
        ],
        "trechos": 1,
    }


def _perm(area_id, ordinal):
    return {
        "area_id": area_id,
        "area": f"Área {area_id}",
        "nivel": f"N{ordinal}",
        "ordinal": ordinal,
    }


class RegraPorForaTests(PermissaoBase):
    """`pode_acessar` reescreve a regra sem usar a query; é o gabarito."""

    def test_area_com_permissao_e_nivel_ate_o_maximo(self):
        self.assertTrue(validacao.pode_acessar(_doc([1], 2), [_perm(1, 2)]))
        self.assertTrue(validacao.pode_acessar(_doc([1], 1), [_perm(1, 2)]))

    def test_nivel_acima_do_maximo_da_area_nao(self):
        self.assertFalse(validacao.pode_acessar(_doc([1], 3), [_perm(1, 2)]))

    def test_sem_permissao_na_area_nao(self):
        self.assertFalse(validacao.pode_acessar(_doc([1], 1), [_perm(2, 4)]))
        self.assertFalse(validacao.pode_acessar(_doc([1], 1), []))

    def test_area_secundaria_basta(self):
        self.assertTrue(validacao.pode_acessar(_doc([1, 2], 2), [_perm(2, 2)]))

    def test_o_maximo_vale_so_para_a_propria_area(self):
        """Nível alto em outra área não libera o documento desta."""
        doc = _doc([1, 2], 3)
        self.assertFalse(validacao.pode_acessar(doc, [_perm(1, 2), _perm(2, 2)]))
        self.assertTrue(validacao.pode_acessar(doc, [_perm(1, 2), _perm(2, 3)]))

    def test_motivo_diz_area_ou_sigilo(self):
        doc = _doc([1, 2], 3)
        motivo = validacao.motivo_do_bloqueio(doc, [_perm(1, 2)])
        self.assertIn("sigilo N3 acima do máximo N2 em Área 1", motivo)
        self.assertIn("sem permissão em Área 2", motivo)

    def test_fonte_da_base_publica_nao_tem_regra(self):
        fonte = {
            "numero": 1,
            "documento_id": None,
            "trecho_id": "7",
            "documento": "MIL-STD-1",
            "titulo": "t",
            "pagina": "3",
            "similaridade": 0.9,
            "citada": True,
        }
        item = validacao._descrever_fonte(fonte, {}, [])
        self.assertIsNone(item["permitida"])
        self.assertEqual(item["origem"], validacao.ORIGEM_PUBLICA)

    def test_fonte_de_documento_fora_do_acervo_disponivel_e_violacao(self):
        fonte = {
            "numero": 1,
            "documento_id": "sumiu",
            "trecho_id": "7",
            "documento": "X",
            "titulo": "t",
            "pagina": "1",
            "similaridade": 0.9,
            "citada": True,
        }
        self.assertIs(validacao._descrever_fonte(fonte, {}, [])["permitida"], False)


class CriteriosTests(PermissaoBase):
    def setUp(self):
        super().setUp()
        self.doc_manut = self.documento_com_trecho(
            self.manutencao,
            self.interno,
            texto="Trecho de manutencao",
            identificador_codigo="MAN-001",
            titulo="Manual de manutencao",
        )
        self.doc_compl = self.documento_com_trecho(
            self.compliance,
            self.interno,
            texto="Trecho de compliance",
            identificador_codigo="CMP-001",
            titulo="Politica de compliance",
        )
        self.conceder(self.eng, self.manutencao, self.interno)
        self.conceder(self.qual, self.compliance, self.interno)
        self.usuarios = [
            {
                "email": "engenharia@akaer.com.br",
                "nome": "Carlos",
                "usuario_id": self.eng.usuario_id,
            },
            {
                "email": "qualidade@akaer.com.br",
                "nome": "Ana",
                "usuario_id": self.qual.usuario_id,
            },
        ]

    def coletar(self, busca=None):
        with (
            mock.patch(EMBED, return_value={"embeddings": [vetor()]}),
            mock.patch(PERGUNTAR_IA, return_value="Resposta com base em [1]."),
        ):
            if busca is None:
                return validacao.coletar(PERGUNTA, "pt-BR", self.usuarios)
            with mock.patch(BUSCA, side_effect=busca):
                return validacao.coletar(PERGUNTA, "pt-BR", self.usuarios)

    def ok_de(self, dados):
        return [c["ok"] for c in dados["criterios"]]

    def test_perfis_diferentes_passa_nos_tres_criterios(self):
        dados = self.coletar()
        eng, qual = dados["usuarios"]

        self.assertEqual(self.ok_de(dados), [True, True, True])
        self.assertEqual({f["codigo"] for f in eng["fontes"]}, {"MAN-001"})
        self.assertEqual({f["codigo"] for f in qual["fontes"]}, {"CMP-001"})
        self.assertFalse(validacao.reprovado(dados))
        self.assertFalse(validacao.inconclusivo(dados))

    def test_mostra_o_que_o_filtro_bloqueou_e_por_que(self):
        dados = self.coletar()
        eng, qual = dados["usuarios"]

        self.assertEqual([b["codigo"] for b in eng["bloqueados"]], ["CMP-001"])
        self.assertEqual(eng["bloqueados"][0]["motivo"], "sem permissão em Compliance")
        self.assertEqual([b["codigo"] for b in qual["bloqueados"]], ["MAN-001"])

    def test_sigilo_acima_do_maximo_aparece_como_bloqueado(self):
        restrito = self.documento_com_trecho(
            self.manutencao,
            self.restrito,
            identificador_codigo="MAN-RES",
            titulo="Manual restrito",
        )
        dados = self.coletar()
        eng = dados["usuarios"][0]

        bloqueado = {b["codigo"]: b for b in eng["bloqueados"]}["MAN-RES"]
        self.assertEqual(
            bloqueado["motivo"], "sigilo RESTRITO acima do máximo INTERNO em Manutenção"
        )
        self.assertNotIn(str(restrito.id), {f["documento_id"] for f in eng["fontes"]})

    def test_filtro_quebrado_reprova_o_terceiro_criterio(self):
        """Se a busca devolvesse trecho de quem não pode ver, a validação
        reprova. Simula o filtro com bug: toda busca roda como um usuário que
        enxerga tudo."""
        self.conceder(self.eng, self.compliance, self.interno)
        todos = self.eng
        real = busca_vetorial.buscar_por_vetor

        def vaza(vetor_, k, modelo, usuario_id):
            return real(vetor_, k, modelo, todos.usuario_id)

        dados = self.coletar(busca=vaza)

        self.assertTrue(validacao.reprovado(dados))
        terceiro = dados["criterios"][2]
        self.assertIs(terceiro["ok"], False)
        self.assertIn("FONTE FORA DA PERMISSÃO", terceiro["detalhe"])
        self.assertIn("Ana: MAN-001", terceiro["detalhe"])

    def test_mesmas_fontes_reprova_o_segundo_criterio(self):
        self.conceder(self.qual, self.manutencao, self.interno)
        self.conceder(self.eng, self.compliance, self.interno)
        dados = self.coletar()
        self.assertIs(dados["criterios"][1]["ok"], False)
        self.assertTrue(validacao.reprovado(dados))

    def test_acervo_vazio_e_inconclusivo_nao_aprovado(self):
        Documento.objects.all().delete()
        dados = self.coletar()

        self.assertIsNone(dados["criterios"][1]["ok"])
        self.assertTrue(validacao.inconclusivo(dados))
        self.assertFalse(validacao.reprovado(dados))

    def test_documento_nao_disponivel_nunca_vira_fonte(self):
        self.documento_com_trecho(
            self.manutencao,
            self.publico,
            status=StatusValidacao.AGUARDANDO_VALIDACAO,
            identificador_codigo="PEND-1",
        )
        dados = self.coletar()
        codigos = {f["codigo"] for u in dados["usuarios"] for f in u["fontes"]}
        self.assertNotIn("PEND-1", codigos)
        self.assertNotIn("PEND-1", {d["codigo"] for d in dados["inventario"]})

    def test_mencao_a_documento_bloqueado_na_resposta_e_so_aviso(self):
        with (
            mock.patch(EMBED, return_value={"embeddings": [vetor()]}),
            mock.patch(PERGUNTAR_IA, return_value="Conforme CMP-001 e [1]."),
        ):
            dados = validacao.coletar(PERGUNTA, "pt-BR", self.usuarios)
        terceiro = dados["criterios"][2]
        self.assertTrue(terceiro["ok"])
        self.assertIn("Atenção", terceiro["detalhe"])
        self.assertIn("CMP-001", terceiro["detalhe"])

    def test_relatorio_markdown_csv_e_json(self):
        dados = self.coletar()
        with tempfile.TemporaryDirectory() as pasta:
            caminhos = validacao.gravar(dados, pasta)
            md = caminhos["md"].read_text(encoding="utf-8")
            csv_bruto = caminhos["csv"].read_bytes()
            json_lido = json.loads(caminhos["json"].read_text(encoding="utf-8"))

        self.assertIn("PASSOU", md)
        self.assertNotIn("REPROVOU", md)
        self.assertIn("Bloqueados para Carlos", md)
        self.assertIn("sem permissão em Compliance", md)
        self.assertTrue(csv_bruto.startswith(b"\xef\xbb\xbf"))  # Excel pt-BR
        self.assertIn(b"usuario;numero;origem", csv_bruto)
        self.assertEqual(json_lido["pergunta"], PERGUNTA)
        self.assertEqual(len(json_lido["usuarios"]), 2)

    def test_pipe_no_titulo_nao_quebra_a_tabela(self):
        self.assertEqual(validacao._celula("a | b\nc"), "a \\| b c")


class ComandoTests(PermissaoBase):
    def setUp(self):
        super().setUp()
        self.doc_manut = self.documento_com_trecho(
            self.manutencao,
            self.interno,
            identificador_codigo="MAN-001",
            titulo="Manual de manutenção",
        )
        self.doc_compl = self.documento_com_trecho(
            self.compliance,
            self.interno,
            identificador_codigo="CMP-001",
            titulo="Política de compliance",
        )
        eng = self.demo("engenharia@akaer.com.br", "Carlos Eduardo", "ENGENHARIA")
        qual = self.demo("qualidade@akaer.com.br", "Ana Souza", "QUALIDADE")
        self.conceder(eng, self.manutencao, self.interno)
        self.conceder(qual, self.compliance, self.interno)

    def demo(self, email, nome, papel):
        uid = uuid.uuid4()
        Papel.objects.using("credenciais_db").get_or_create(
            codigo=papel, defaults={"descricao": papel.title()}
        )
        Usuario.objects.using("credenciais_db").create(
            id=uid, nome=nome, email=email, senha_hash="x", papel_id=papel
        )
        return PerfilOperacional.objects.create(
            usuario_id=uid, matricula=f"AK-{uid.hex[:8]}"
        )

    def rodar(self, pasta, **extra):
        saida, erro = StringIO(), StringIO()
        with (
            mock.patch(EMBED, return_value={"embeddings": [vetor()]}),
            mock.patch(PERGUNTAR_IA, return_value="Resposta com base em [1]."),
        ):
            call_command(
                "validar_permissao_demo",
                "--pergunta",
                PERGUNTA,
                "--saida",
                pasta,
                stdout=saida,
                stderr=erro,
                **extra,
            )
        return saida.getvalue(), erro.getvalue()

    def test_roda_com_os_usuarios_demo_e_grava_o_relatorio(self):
        with tempfile.TemporaryDirectory() as pasta:
            saida, _ = self.rodar(pasta)
            arquivos = sorted(p.suffix for p in Path(pasta).iterdir())

        self.assertEqual(arquivos, [".csv", ".json", ".md"])
        self.assertIn("[PASSOU]", saida)
        self.assertIn("1 fonte(s) do sistema", saida)

    def test_saida_do_console_e_ascii_para_o_windows(self):
        with tempfile.TemporaryDirectory() as pasta:
            saida, erro = self.rodar(pasta)
        self.assertTrue(saida.isascii(), saida)
        self.assertTrue(erro.isascii(), erro)

    def test_usuario_demo_inexistente_manda_rodar_o_seed(self):
        Usuario.objects.using("credenciais_db").filter(
            email="qualidade@akaer.com.br"
        ).delete()
        with (
            tempfile.TemporaryDirectory() as pasta,
            self.assertRaises(CommandError) as ctx,
        ):
            self.rodar(pasta)
        self.assertIn("seed_usuarios_demo", str(ctx.exception))
        self.assertIn("qualidade@akaer.com.br", str(ctx.exception))

    def test_filtro_com_vazamento_sai_com_erro_mas_grava_o_relatorio(self):
        real = busca_vetorial.buscar_por_vetor
        todos = self.demo("todos@akaer.com.br", "Todos", "ADMINISTRADOR")
        self.conceder(todos, self.manutencao, self.interno)
        self.conceder(todos, self.compliance, self.interno)

        def vaza(vetor_, k, modelo, usuario_id):
            return real(vetor_, k, modelo, todos.usuario_id)

        with tempfile.TemporaryDirectory() as pasta:
            with (
                mock.patch(BUSCA, side_effect=vaza),
                self.assertRaises(CommandError) as ctx,
            ):
                self.rodar(pasta)
            md = next(Path(pasta).glob("*.md")).read_text(encoding="utf-8")

        self.assertIn("REPROVADA", str(ctx.exception))
        self.assertIn("REPROVOU", md)

    def test_acervo_sem_documentos_avisa_inconclusivo_sem_falhar(self):
        Documento.objects.all().delete()
        with tempfile.TemporaryDirectory() as pasta:
            saida, erro = self.rodar(pasta)
        self.assertIn("INCONCLUSIVA", erro)
        self.assertIn("[INCONCLUSIVO]", saida)
