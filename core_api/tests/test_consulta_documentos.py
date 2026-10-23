"""
Testes dos endpoints de consulta (S2-35): fila de pendentes, meus documentos
e detalhe. Um teste por critério de aceite.

TransactionTestCase pelo mesmo motivo da S2-33 (triggers deferrable de
documento só disparam no COMMIT) e com os dois bancos, porque o nome do autor
vive em `credenciais_db`.
"""

import uuid
from datetime import timedelta

from django.db import connections
from django.test import Client, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core_api.models import (
    AcaoValidacao,
    Documento,
    HistoricoValidacao,
    NivelSigilo,
    PerfilOperacional,
    StatusProcessamento,
    StatusValidacao,
)
from core_api.services.auth.autenticacao import AuthService
from core_api.tests.test_documento import _documento_valido
from core_api.utils.jwt_auth import generate_token

ADMIN = "ADMINISTRADOR"
AGUARDANDO = StatusValidacao.AGUARDANDO_VALIDACAO


class ConsultaBase(TransactionTestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        self.admin, self.token_admin = self.usuario(ADMIN, "Ricardo Mendes")
        self.autor, self.token_autor = self.usuario("ENGENHARIA", "Carlos Eduardo")
        self.outro, self.token_outro = self.usuario("QUALIDADE", "Ana Souza")

    # -- fábricas -------------------------------------------------------
    def usuario(self, role, nome):
        """Usuário real nos dois bancos (credenciais + perfil) e o token dele."""
        email = f"{uuid.uuid4().hex[:8]}@akaer.com.br"
        dados = AuthService.create_user(
            {"email": email, "password": "x12345", "name": nome, "role": role}
        )
        perfil = PerfilOperacional.objects.get(pk=dados["id"])
        return perfil, generate_token(dados)

    def doc(self, autor, status=AGUARDANDO, **extra):
        doc, _ = _documento_valido(
            identificador_codigo=f"DOC-{uuid.uuid4().hex[:8]}",
            usuario_upload=autor,
            responsavel=autor,
            **extra,
        )
        if status != AGUARDANDO:
            Documento.objects.filter(pk=doc.pk).update(status_validacao=status)
            doc.refresh_from_db()
        return doc

    def datar(self, doc, minutos_atras):
        Documento.objects.filter(pk=doc.pk).update(
            criado_em=timezone.now() - timedelta(minutes=minutos_atras)
        )

    def historico(self, doc, usuario, acao, comentario="", nivel=None):
        return HistoricoValidacao.objects.create(
            documento=doc,
            usuario=usuario,
            acao=acao,
            comentario=comentario,
            nivel_sigilo=nivel,
        )

    def historico_em(self, doc, usuario, acao, minutos_atras):
        """Insere uma linha com data explícita (criado_em é auto_now_add e a
        tabela é só inserção, então a data só se escolhe num INSERT direto)."""
        with connections["default"].cursor() as c:
            c.execute(
                "INSERT INTO historico_validacao "
                "(documento_id, acao, usuario_id, comentario, criado_em) "
                "VALUES (%s, %s, %s, '', %s)",
                [
                    doc.pk,
                    acao,
                    usuario.pk,
                    timezone.now() - timedelta(minutes=minutos_atras),
                ],
            )

    # -- chamadas -------------------------------------------------------
    def get(self, url, token=None):
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(url, **extra)

    def ids(self, resposta):
        return [r["id"] for r in resposta.json()["results"]]


class FilaDePendentesTestCase(ConsultaBase):
    URL = "/api/documents/pendentes/"

    def test_so_traz_documentos_aguardando_validacao(self):
        pendente = self.doc(self.autor)
        self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        self.doc(self.autor, StatusValidacao.DISPONIVEL)
        r = self.get(self.URL, self.token_admin)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.ids(r), [str(pendente.id)])
        self.assertEqual(r.json()["results"][0]["status_validacao"], AGUARDANDO)

    def test_validador_nao_ve_os_proprios_envios_na_fila(self):
        meu = self.doc(self.admin)
        alheio = self.doc(self.autor)
        r = self.get(self.URL, self.token_admin)
        self.assertEqual(self.ids(r), [str(alheio.id)])
        self.assertNotIn(str(meu.id), self.ids(r))

    def test_envio_de_um_validador_aparece_para_outro_validador(self):
        outro_admin, token_outro_admin = self.usuario(ADMIN, "Outro Admin")
        doc = self.doc(self.admin)
        r = self.get(self.URL, token_outro_admin)
        self.assertEqual(self.ids(r), [str(doc.id)])

    def test_ordem_do_mais_antigo_para_o_mais_novo(self):
        novo, velho, medio = (
            self.doc(self.autor),
            self.doc(self.autor),
            self.doc(self.autor),
        )
        self.datar(velho, 300)
        self.datar(medio, 200)
        self.datar(novo, 100)
        r = self.get(self.URL, self.token_admin)
        self.assertEqual(self.ids(r), [str(velho.id), str(medio.id), str(novo.id)])

    def test_paginada_de_20_em_20(self):
        for i in range(25):
            self.doc(self.autor)
        p1 = self.get(self.URL, self.token_admin).json()
        self.assertEqual(
            (p1["count"], p1["page"], p1["page_size"], p1["total_pages"]),
            (25, 1, 20, 2),
        )
        self.assertEqual(len(p1["results"]), 20)
        p2 = self.get(self.URL + "?page=2", self.token_admin).json()
        self.assertEqual(len(p2["results"]), 5)
        # as páginas não se repetem
        ids1 = {r["id"] for r in p1["results"]}
        self.assertTrue(ids1.isdisjoint({r["id"] for r in p2["results"]}))
        # além da última: vazia, mas com os totais certos
        p3 = self.get(self.URL + "?page=3", self.token_admin).json()
        self.assertEqual((p3["results"], p3["count"]), ([], 25))

    def test_pagina_invalida_e_400(self):
        for valor in ("0", "-1", "abc", "1.5"):
            r = self.get(self.URL + f"?page={valor}", self.token_admin)
            self.assertEqual(r.status_code, 400, valor)
            self.assertEqual(r.json()["field"], "page")

    def test_traz_status_processamento(self):
        doc = self.doc(self.autor)
        Documento.objects.filter(pk=doc.pk).update(
            status_processamento=StatusProcessamento.PROCESSANDO
        )
        item = self.get(self.URL, self.token_admin).json()["results"][0]
        self.assertEqual(item["status_processamento"], "PROCESSANDO")

    def test_autor_aparece_so_pelo_nome_sem_email(self):
        self.doc(self.autor)
        r = self.get(self.URL, self.token_admin)
        self.assertEqual(r.json()["results"][0]["autor"], "Carlos Eduardo")
        self.assertNotIn("@", r.content.decode())

    def test_so_validador_acessa_a_fila(self):
        for token in (self.token_autor, self.token_outro):
            self.assertEqual(self.get(self.URL, token).status_code, 403)
        self.assertEqual(self.get(self.URL).status_code, 401)

    def test_so_aceita_get(self):
        r = self.client.post(self.URL, HTTP_AUTHORIZATION=f"Bearer {self.token_admin}")
        self.assertEqual(r.status_code, 405)


class MeusDocumentosTestCase(ConsultaBase):
    URL = "/api/documents/meus/"

    def test_autor_ve_todos_os_envios_com_o_estado_correto(self):
        a = self.doc(self.autor, AGUARDANDO)
        b = self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        c = self.doc(self.autor, StatusValidacao.DISPONIVEL)
        self.doc(self.outro)  # de outra pessoa: não aparece
        r = self.get(self.URL, self.token_autor)
        self.assertEqual(r.status_code, 200, r.content)
        estados = {x["id"]: x["status_validacao"] for x in r.json()["results"]}
        self.assertEqual(
            estados,
            {
                str(a.id): AGUARDANDO,
                str(b.id): StatusValidacao.DEVOLVIDO,
                str(c.id): StatusValidacao.DISPONIVEL,
            },
        )

    def test_validador_ve_so_os_proprios_envios_aqui(self):
        meu = self.doc(self.admin)
        self.doc(self.autor)
        self.assertEqual(self.ids(self.get(self.URL, self.token_admin)), [str(meu.id)])

    def test_traz_o_ultimo_comentario_de_devolucao(self):
        doc = self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        self.historico(doc, self.admin, AcaoValidacao.DEVOLVIDO, "primeira devolução")
        self.historico(doc, self.admin, AcaoValidacao.REENVIADO, "ajustei")
        self.historico(doc, self.admin, AcaoValidacao.DEVOLVIDO, "segunda devolução")
        item = self.get(self.URL, self.token_autor).json()["results"][0]
        self.assertEqual(item["ultimo_comentario_devolucao"], "segunda devolução")

    def test_comentario_sobrevive_ao_reenvio(self):
        # reenviado: voltou a aguardar, o campo do documento foi limpo, mas o
        # autor ainda quer ver o que lhe pediram.
        doc = self.doc(self.autor, AGUARDANDO)
        self.historico(doc, self.admin, AcaoValidacao.DEVOLVIDO, "falta a revisão")
        self.historico(doc, self.autor, AcaoValidacao.REENVIADO)
        item = self.get(self.URL, self.token_autor).json()["results"][0]
        self.assertEqual(item["status_validacao"], AGUARDANDO)
        self.assertEqual(item["ultimo_comentario_devolucao"], "falta a revisão")

    def test_sem_devolucao_o_comentario_e_nulo(self):
        self.doc(self.autor)
        item = self.get(self.URL, self.token_autor).json()["results"][0]
        self.assertIsNone(item["ultimo_comentario_devolucao"])

    def test_filtro_por_status(self):
        self.doc(self.autor, AGUARDANDO)
        dev = self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        for valor in ("DEVOLVIDO", "devolvido", " Devolvido "):
            r = self.get(self.URL + f"?status={valor.strip()}", self.token_autor)
            self.assertEqual(self.ids(r), [str(dev.id)], valor)

    def test_status_invalido_e_400(self):
        r = self.get(self.URL + "?status=QUALQUER", self.token_autor)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["field"], "status")

    def test_paginada_de_20_em_20_e_status_processamento(self):
        for _ in range(21):
            self.doc(self.autor)
        p1 = self.get(self.URL, self.token_autor).json()
        self.assertEqual(
            (p1["count"], len(p1["results"]), p1["total_pages"]), (21, 20, 2)
        )
        self.assertTrue(all("status_processamento" in x for x in p1["results"]))
        self.assertEqual(p1["results"][0]["status_processamento"], "PENDENTE")
        p2 = self.get(self.URL + "?page=2", self.token_autor).json()
        self.assertEqual(len(p2["results"]), 1)

    def test_contagens_por_status_ignoram_o_filtro(self):
        self.doc(self.autor, AGUARDANDO)
        self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        self.doc(self.autor, StatusValidacao.DISPONIVEL)
        esperado = {
            "AGUARDANDO_VALIDACAO": 1,
            "DEVOLVIDO": 2,
            "DISPONIVEL": 1,
            "TODOS": 4,
        }
        for url in (self.URL, self.URL + "?status=DISPONIVEL&page=2"):
            r = self.get(url, self.token_autor)
            self.assertEqual(r.json()["contagens"], esperado, url)

    def test_contagens_so_dos_proprios_envios_e_zeradas_sem_envio(self):
        self.doc(self.outro, StatusValidacao.DEVOLVIDO)
        r = self.get(self.URL, self.token_autor)
        self.assertEqual(
            r.json()["contagens"],
            {"AGUARDANDO_VALIDACAO": 0, "DEVOLVIDO": 0, "DISPONIVEL": 0, "TODOS": 0},
        )

    def test_nao_vaza_email(self):
        self.doc(self.autor)
        r = self.get(self.URL, self.token_autor)
        self.assertEqual(r.json()["results"][0]["autor"], "Carlos Eduardo")
        self.assertNotIn("@", r.content.decode())

    def test_exige_autenticacao(self):
        self.assertEqual(self.get(self.URL).status_code, 401)


class DetalheTestCase(ConsultaBase):
    def url(self, doc):
        return f"/api/documents/{doc.id}/"

    def test_usuario_comum_recebe_404_em_pendente_de_outra_pessoa(self):
        doc = self.doc(self.autor)
        r = self.get(self.url(doc), self.token_outro)
        self.assertEqual(r.status_code, 404)

    def test_404_tambem_para_devolvido_de_outra_pessoa(self):
        doc = self.doc(self.autor, StatusValidacao.DEVOLVIDO)
        self.assertEqual(self.get(self.url(doc), self.token_outro).status_code, 404)

    def test_404_nao_denuncia_que_o_documento_existe(self):
        doc = self.doc(self.autor)
        proibido = self.get(self.url(doc), self.token_outro)
        inexistente = self.get(f"/api/documents/{uuid.uuid4()}/", self.token_outro)
        self.assertEqual(proibido.status_code, inexistente.status_code)
        self.assertEqual(proibido.json(), inexistente.json())

    def test_autor_ve_o_proprio_documento_em_qualquer_estado(self):
        for estado in StatusValidacao.values:
            doc = self.doc(self.autor, estado)
            r = self.get(self.url(doc), self.token_autor)
            self.assertEqual(r.status_code, 200, estado)

    def test_validador_ve_documento_de_qualquer_estado(self):
        for estado in StatusValidacao.values:
            doc = self.doc(self.autor, estado)
            self.assertEqual(self.get(self.url(doc), self.token_admin).status_code, 200)

    def test_documento_disponivel_e_visto_por_qualquer_usuario_autenticado(self):
        doc = self.doc(self.autor, StatusValidacao.DISPONIVEL)
        self.assertEqual(self.get(self.url(doc), self.token_outro).status_code, 200)

    def test_exige_autenticacao_e_404_para_id_inexistente(self):
        doc = self.doc(self.autor)
        self.assertEqual(self.get(self.url(doc)).status_code, 401)
        r = self.get(f"/api/documents/{uuid.uuid4()}/", self.token_admin)
        self.assertEqual(r.status_code, 404)

    def test_historico_vem_ordenado_por_data(self):
        doc = self.doc(self.autor)
        # inseridos fora de ordem de propósito: a ordem de inserção (e do id)
        # não pode ser a que manda.
        self.historico_em(doc, self.autor, AcaoValidacao.REENVIADO, 20)
        self.historico_em(doc, self.autor, AcaoValidacao.ENVIADO, 30)
        self.historico_em(doc, self.admin, AcaoValidacao.DEVOLVIDO, 10)
        h = self.get(self.url(doc), self.token_admin).json()["historico"]
        self.assertEqual([x["acao"] for x in h], ["ENVIADO", "REENVIADO", "DEVOLVIDO"])
        datas = [x["criado_em"] for x in h]
        self.assertEqual(datas, sorted(datas))

    def test_historico_traz_nome_comentario_e_nivel_sem_email(self):
        doc = self.doc(self.autor)
        final = NivelSigilo.objects.get_or_create(
            nome="RESTRITO", defaults={"ordinal": 3}
        )[0]
        self.historico(doc, self.autor, AcaoValidacao.ENVIADO)
        self.historico(doc, self.admin, AcaoValidacao.APROVADO, nivel=final)
        r = self.get(self.url(doc), self.token_admin)
        h = r.json()["historico"]
        self.assertEqual(
            [x["usuario"] for x in h], ["Carlos Eduardo", "Ricardo Mendes"]
        )
        self.assertEqual(h[1]["nivel_sigilo"], "RESTRITO")
        self.assertIsNone(h[0]["nivel_sigilo"])
        self.assertNotIn("@", r.content.decode())

    def test_traz_metadados_status_processamento_e_autor_so_pelo_nome(self):
        doc = self.doc(self.autor)
        Documento.objects.filter(pk=doc.pk).update(
            status_processamento=StatusProcessamento.ERRO,
            erro_processamento="OCR falhou",
        )
        d = self.get(self.url(doc), self.token_admin).json()
        self.assertEqual(d["id"], str(doc.id))
        self.assertEqual(d["titulo"], doc.titulo)
        self.assertEqual(d["status_validacao"], AGUARDANDO)
        self.assertEqual(d["status_processamento"], "ERRO")
        self.assertEqual(d["erro_processamento"], "OCR falhou")
        self.assertEqual(d["autor"], "Carlos Eduardo")
        self.assertIsNone(d["validado_por"])
        self.assertEqual(d["historico"], [])
        self.assertNotIn("@", str(d))

    def test_validado_por_aparece_pelo_nome(self):
        doc = self.doc(self.autor, StatusValidacao.DISPONIVEL)
        Documento.objects.filter(pk=doc.pk).update(
            validado_por=self.admin, validado_em=timezone.now()
        )
        d = self.get(self.url(doc), self.token_outro).json()
        self.assertEqual(d["validado_por"], "Ricardo Mendes")
        self.assertIsNotNone(d["validado_em"])


class DesempenhoTestCase(ConsultaBase):
    def _consultas(self, url, token):
        with (
            CaptureQueriesContext(connections["default"]) as a,
            CaptureQueriesContext(connections["credenciais_db"]) as b,
        ):
            self.assertEqual(self.get(url, token).status_code, 200)
        return len(a), len(b)

    def test_lista_nao_faz_consulta_por_linha(self):
        for _ in range(2):
            self.doc(self.autor)
        poucas = self._consultas("/api/documents/pendentes/", self.token_admin)
        for _ in range(8):
            self.doc(self.autor)
        muitas = self._consultas("/api/documents/pendentes/", self.token_admin)
        self.assertEqual(poucas, muitas)
        self.assertEqual(muitas[1], 1)  # nomes: 1 consulta em credenciais_db

    def test_meus_nao_faz_consulta_por_linha(self):
        for _ in range(2):
            self.doc(self.autor)
        poucas = self._consultas("/api/documents/meus/", self.token_autor)
        for _ in range(8):
            self.doc(self.autor)
        muitas = self._consultas("/api/documents/meus/", self.token_autor)
        self.assertEqual(poucas, muitas)
