"""
Testes da consulta do log de auditoria (AKA-81, RF29):

    GET /api/auditoria/logs/?usuario=&de=&ate=&acao=&page=
    GET /api/auditoria/logs/exportar/?usuario=&de=&ate=&acao=

Um bloco por critério de aceite: permissão (só ADMINISTRADOR), lista
(ordem, nomes, paginação, as duas trilhas), filtros e exportação CSV.

O log é só de inclusão (AKA-80), então a data de cada linha é escolhida
adiantando o relógio na hora de gravar, nunca alterando o criado_em.
"""

import csv
import io
import uuid
from datetime import datetime, timezone
from unittest import mock

from django.test import Client, TransactionTestCase

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    EntidadeAuditoria,
    LogAcesso,
    LogAuditoria,
)
from core_api.services.auditoria import registrar_acao
from core_api.services.auth.autenticacao import AuthService
from core_api.utils.jwt_auth import generate_token

URL = "/api/auditoria/logs/"
URL_CSV = "/api/auditoria/logs/exportar/"


def em(ano, mes, dia, hora=12, minuto=0):
    """Instante em UTC, para gravar o log naquele momento."""
    return datetime(ano, mes, dia, hora, minuto, tzinfo=timezone.utc)


class ConsultaAuditoriaBase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        self.admin, self.token_admin = self.usuario("Ana Admin", "ADMINISTRADOR")
        self.eng, self.token_eng = self.usuario("Edu Engenharia", "ENGENHARIA")
        # Criar os usuários pode ter gravado linhas; os testes partem do zero
        # olhando só o que gravam (ids acima destes).
        self.ultimo_auditoria = (
            LogAuditoria.objects.order_by("-id").values_list("id", flat=True).first()
            or 0
        )

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

    def acao(self, quando, usuario_id, acao=AcaoAuditoria.LOGIN, registro=None):
        with mock.patch("django.utils.timezone.now", return_value=quando):
            return registrar_acao(
                usuario_id,
                acao,
                EntidadeAuditoria.USUARIO
                if registro is None
                else EntidadeAuditoria.DOCUMENTO,
                registro,
            )

    def acesso(self, quando, usuario_id, documento_id=None):
        with mock.patch("django.utils.timezone.now", return_value=quando):
            return LogAcesso.objects.create(
                documento_id=documento_id or uuid.uuid4(),
                usuario_id=usuario_id,
                acao=AcaoAcesso.VISUALIZACAO,
            )

    def get(self, url=URL, token="admin", **params):
        token = {"admin": self.token_admin, "eng": self.token_eng}.get(token, token)
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(url, params, **extra)

    def listar(self, **params):
        resposta = self.get(**params)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        corpo = resposta.json()
        # Ignora linhas gravadas pela criação dos usuários no setUp.
        corpo["results"] = [
            r
            for r in corpo["results"]
            if not (
                r["id"].startswith("auditoria-")
                and int(r["id"].split("-")[1]) <= self.ultimo_auditoria
            )
        ]
        return corpo

    def csv(self, **params):
        resposta = self.get(URL_CSV, **params)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        texto = resposta.content.decode("utf-8")
        self.assertTrue(texto.startswith("﻿"), "CSV sem BOM")
        return resposta, list(csv.reader(io.StringIO(texto[1:]), delimiter=";"))


class PermissaoTests(ConsultaAuditoriaBase):
    def test_administrador_consulta_e_exporta(self):
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.get(URL_CSV).status_code, 200)

    def test_outros_papeis_recebem_403(self):
        _, token_qualidade = self.usuario("Quésia Qualidade", "QUALIDADE")
        for token in (self.token_eng, token_qualidade):
            for url in (URL, URL_CSV):
                with self.subTest(url=url):
                    self.assertEqual(self.get(url, token=token).status_code, 403)

    def test_sem_token_recebe_401(self):
        for url in (URL, URL_CSV):
            with self.subTest(url=url):
                self.assertEqual(self.get(url, token=None).status_code, 401)

    def test_so_leitura_sem_metodos_de_escrita(self):
        cabecalho = {"HTTP_AUTHORIZATION": f"Bearer {self.token_admin}"}
        for metodo in ("post", "put", "patch", "delete"):
            for url in (URL, URL_CSV):
                with self.subTest(metodo=metodo, url=url):
                    resposta = getattr(self.client, metodo)(url, **cabecalho)
                    self.assertEqual(resposta.status_code, 405)


class ListaTests(ConsultaAuditoriaBase):
    def test_mais_recente_primeiro_com_as_duas_trilhas(self):
        doc = uuid.uuid4()
        self.acao(em(2026, 11, 10), self.eng["id"])
        self.acesso(em(2026, 11, 12), self.eng["id"], doc)
        self.acao(em(2026, 11, 11), self.eng["id"], AcaoAuditoria.LOGIN_FALHOU)

        resultados = self.listar()["results"]

        self.assertEqual(
            [r["acao"] for r in resultados],
            [AcaoAcesso.VISUALIZACAO, AcaoAuditoria.LOGIN_FALHOU, AcaoAuditoria.LOGIN],
        )
        acesso = resultados[0]
        self.assertEqual(acesso["entidade"], EntidadeAuditoria.DOCUMENTO)
        self.assertEqual(acesso["registro_id"], str(doc))
        self.assertEqual(acesso["acao_rotulo"], "Visualização")
        self.assertEqual(acesso["entidade_rotulo"], "Documento")
        self.assertEqual(resultados[1]["acao_rotulo"], "Login falhou")
        self.assertEqual(acesso["criado_em"], "2026-11-12T12:00:00+00:00")

    def test_nome_vem_das_credenciais_e_removido_ou_sem_usuario(self):
        self.acao(em(2026, 11, 10, 3), self.eng["id"])
        self.acao(em(2026, 11, 10, 2), uuid.uuid4())  # usuário excluído
        self.acao(em(2026, 11, 10, 1), None)  # e-mail que não existe

        nomes = [r["usuario_nome"] for r in self.listar()["results"]]

        self.assertEqual(
            nomes, ["Edu Engenharia", "Usuário removido", "Não identificado"]
        )

    def test_resposta_nao_traz_email(self):
        self.acao(em(2026, 11, 10), self.eng["id"])
        self.assertNotIn("@", self.get().content.decode())

    def test_paginada_de_20_em_20(self):
        for minuto in range(25):
            self.acao(em(2026, 11, 10, 12, minuto), self.eng["id"])

        pagina1 = self.get(page=1).json()
        pagina2 = self.get(page=2).json()

        self.assertEqual(pagina1["page_size"], 20)
        self.assertEqual(len(pagina1["results"]), 20)
        self.assertGreaterEqual(pagina1["count"], 25)
        self.assertEqual(pagina1["total_pages"], 2)
        # Sem repetição entre as páginas.
        ids = [r["id"] for r in pagina1["results"] + pagina2["results"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_traz_as_opcoes_de_acao_das_duas_trilhas(self):
        valores = {a["valor"] for a in self.get().json()["acoes"]}
        self.assertTrue(set(AcaoAuditoria.values) <= valores)
        self.assertTrue(set(AcaoAcesso.values) <= valores)

    def test_consultar_nao_grava_nada_no_log(self):
        antes = (LogAuditoria.objects.count(), LogAcesso.objects.count())
        self.get()
        self.get(URL_CSV)
        self.assertEqual(
            (LogAuditoria.objects.count(), LogAcesso.objects.count()), antes
        )


class FiltroTests(ConsultaAuditoriaBase):
    def test_por_usuario_nas_duas_trilhas(self):
        self.acao(em(2026, 11, 10), self.eng["id"])
        self.acesso(em(2026, 11, 10), self.eng["id"])
        self.acao(em(2026, 11, 10), self.admin["id"])

        resultados = self.listar(usuario=self.eng["id"])["results"]

        self.assertEqual(len(resultados), 2)
        self.assertTrue(all(r["usuario_id"] == self.eng["id"] for r in resultados))

    def test_periodo_inclusivo_no_horario_de_brasilia(self):
        # 10/11 02:30 UTC = 09/11 23:30 em Brasília.
        noite_de_9 = self.acao(em(2026, 11, 10, 2, 30), self.eng["id"])
        dia_10 = self.acao(em(2026, 11, 10, 12), self.eng["id"])
        # 11/11 02:59 UTC = 10/11 23:59 em Brasília.
        fim_de_10 = self.acao(em(2026, 11, 11, 2, 59), self.eng["id"])
        dia_11 = self.acao(em(2026, 11, 11, 12), self.eng["id"])

        def ids(**filtro):
            return {r["id"] for r in self.listar(**filtro)["results"]}

        self.assertEqual(
            ids(de="2026-11-10", ate="2026-11-10"),
            {f"auditoria-{dia_10.id}", f"auditoria-{fim_de_10.id}"},
        )
        self.assertEqual(ids(ate="2026-11-09"), {f"auditoria-{noite_de_9.id}"})
        self.assertEqual(ids(de="2026-11-11"), {f"auditoria-{dia_11.id}"})

    def test_por_acao_de_cada_trilha(self):
        self.acao(em(2026, 11, 10), self.eng["id"], AcaoAuditoria.LOGIN)
        self.acao(em(2026, 11, 10), self.eng["id"], AcaoAuditoria.APROVADO, "x")
        self.acesso(em(2026, 11, 10), self.eng["id"])

        acoes = {r["acao"] for r in self.listar(acao="APROVADO")["results"]}
        self.assertEqual(acoes, {AcaoAuditoria.APROVADO})
        acoes = {r["acao"] for r in self.listar(acao="visualizacao")["results"]}
        self.assertEqual(acoes, {AcaoAcesso.VISUALIZACAO})

    def test_filtros_combinados(self):
        alvo = self.acao(em(2026, 11, 10), self.eng["id"], AcaoAuditoria.LOGIN)
        self.acao(em(2026, 11, 10), self.admin["id"], AcaoAuditoria.LOGIN)
        self.acao(em(2026, 11, 10), self.eng["id"], AcaoAuditoria.LOGIN_FALHOU)
        self.acao(em(2026, 11, 12), self.eng["id"], AcaoAuditoria.LOGIN)

        resultados = self.listar(
            usuario=self.eng["id"], acao="LOGIN", de="2026-11-10", ate="2026-11-11"
        )["results"]

        self.assertEqual([r["id"] for r in resultados], [f"auditoria-{alvo.id}"])

    def test_parametros_invalidos_devolvem_400_com_o_campo(self):
        casos = [
            ({"usuario": "nao-e-uuid"}, "usuario"),
            ({"de": "10/11/2026"}, "de"),
            ({"ate": "2026-13-01"}, "ate"),
            ({"de": "2026-11-12", "ate": "2026-11-10"}, "ate"),
            ({"acao": "APAGAR_TUDO"}, "acao"),
            ({"page": "abc"}, "page"),
        ]
        for params, campo in casos:
            with self.subTest(params=params):
                resposta = self.get(**params)
                self.assertEqual(resposta.status_code, 400)
                self.assertEqual(resposta.json()["field"], campo)
                if campo != "page":
                    self.assertEqual(self.get(URL_CSV, **params).status_code, 400)


class ExportacaoCsvTests(ConsultaAuditoriaBase):
    def test_csv_do_resultado_filtrado_sem_paginacao(self):
        for minuto in range(25):
            self.acao(em(2026, 11, 10, 15, minuto), self.eng["id"])
        self.acao(em(2026, 11, 10, 15), self.admin["id"])

        resposta, linhas = self.csv(usuario=self.eng["id"])

        self.assertEqual(resposta["Content-Type"], "text/csv; charset=utf-8")
        self.assertRegex(
            resposta["Content-Disposition"],
            r'^attachment; filename="log-auditoria-\d{4}-\d{2}-\d{2}\.csv"$',
        )
        self.assertEqual(resposta["Cache-Control"], "no-store")
        cabecalho, *dados = linhas
        self.assertEqual(
            cabecalho,
            [
                "Data/hora (Brasília)",
                "Usuário",
                "ID do usuário",
                "Ação",
                "Entidade",
                "Registro",
            ],
        )
        self.assertEqual(len(dados), 25)
        # Mais recente primeiro, no horário de Brasília (15:24 UTC = 12:24).
        self.assertEqual(
            dados[0][:5],
            [
                "10/11/2026 12:24:00",
                "Edu Engenharia",
                self.eng["id"],
                "Login",
                "Usuário",
            ],
        )

    def test_csv_traz_acessos_e_usuario_removido(self):
        doc = uuid.uuid4()
        removido = uuid.uuid4()
        self.acesso(em(2026, 11, 10), removido, doc)

        _, (_, *dados) = self.csv(acao="VISUALIZACAO")

        self.assertEqual(
            dados,
            [
                [
                    "10/11/2026 09:00:00",
                    "Usuário removido",
                    str(removido),
                    "Visualização",
                    "Documento",
                    str(doc),
                ]
            ],
        )

    def test_nome_que_parece_formula_nao_vira_formula_no_excel(self):
        malicioso, _ = self.usuario('=HYPERLINK("http://x","clique")', "ENGENHARIA")
        self.acao(em(2026, 11, 10), malicioso["id"])

        _, (_, *dados) = self.csv(usuario=malicioso["id"])

        self.assertEqual(dados[0][1], '\'=HYPERLINK("http://x","clique")')
