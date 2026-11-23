"""
Testes da edição dos dados pessoais (S2-49): PATCH /api/auth/me/,
POST /api/auth/confirmar-email/ e POST /api/auth/reenviar-confirmacao/.
Um teste por critério de aceite, mais as regras de cada campo.

O backend de e-mail dos testes é o locmem do Django (`mail.outbox`).
"""

import hashlib
import json
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core_api.services.auth.autenticacao import AuthService
from core_api.utils.jwt_auth import generate_token
from credenciais.models import Papel, Usuario

ME = "/api/auth/me/"
CONFIRMAR = "/api/auth/confirmar-email/"
REENVIAR = "/api/auth/reenviar-confirmacao/"
NOVO_EMAIL = "novo.email@akaer.com.br"


@override_settings(
    FRONTEND_URL="http://front.test",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class DadosPessoaisBase(TestCase):
    databases = {"default", "credenciais_db"}

    def setUp(self):
        self.client = Client()
        self.dados_ana, self.token_ana = self.criar("Ana Souza", Papel.ENGENHARIA)
        self.dados_bia, self.token_bia = self.criar("Bia Campos", Papel.QUALIDADE)

    def criar(self, nome, papel):
        dados = AuthService.create_user(
            {
                "email": f"{uuid.uuid4().hex[:8]}@akaer.com.br",
                "password": "x12345",
                "name": nome,
                "role": papel,
            }
        )
        return dados, generate_token(dados)

    def usuario(self, dados=None):
        return Usuario.objects.using("credenciais_db").get(
            pk=(dados or self.dados_ana)["id"]
        )

    def patch(self, corpo, token="padrao"):
        token = self.token_ana if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.patch(
            ME, data=json.dumps(corpo), content_type="application/json", **extra
        )

    def confirmar(self, token):
        return self.client.post(
            CONFIRMAR,
            data=json.dumps({"token": token}),
            content_type="application/json",
        )

    def reenviar(self, token="padrao"):
        token = self.token_ana if token == "padrao" else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(REENVIAR, **extra)

    def login(self, email, senha="x12345"):
        return self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": email, "password": senha}),
            content_type="application/json",
        )

    def token_do_ultimo_link(self):
        """Token que o usuário veria no e-mail novo mais recente."""
        for mensagem in reversed(mail.outbox):
            achado = re.search(r"confirmar-email\?token=([\w-]+)", mensagem.body)
            if achado:
                return achado.group(1)
        self.fail("Nenhum e-mail com link de confirmação foi enviado.")

    def pedir_troca(self, email=NOVO_EMAIL):
        resposta = self.patch({"email": email})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        return self.token_do_ultimo_link()

    def liberar_intervalo(self):
        """Simula 1 minuto passado desde o último link."""
        Usuario.objects.using("credenciais_db").filter(pk=self.dados_ana["id"]).update(
            confirmacao_enviada_em=timezone.now() - timedelta(minutes=2)
        )


class NomeTests(DadosPessoaisBase):
    def test_usuario_altera_o_proprio_nome(self):
        resposta = self.patch({"nome": "  Ana Souza Lima "})

        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(resposta.json()["user"]["name"], "Ana Souza Lima")
        self.assertEqual(self.usuario().nome, "Ana Souza Lima")
        self.assertEqual(mail.outbox, [])  # nome não dispara e-mail

    def test_nome_fora_de_3_a_150_caracteres(self):
        for nome in ["", "  ", "ab", "x" * 151, 123, None]:
            with self.subTest(nome=nome):
                resposta = self.patch({"nome": nome})
                self.assertEqual(resposta.status_code, 400, resposta.content)
                self.assertEqual(resposta.json()["field"], "nome")
        self.assertEqual(self.usuario().nome, "Ana Souza")

    def test_limites_do_nome_valem(self):
        self.assertEqual(self.patch({"nome": "Ana"}).status_code, 200)
        self.assertEqual(self.patch({"nome": "x" * 150}).status_code, 200)

    def test_nome_e_email_sao_opcionais_mas_um_dos_dois_precisa_vir(self):
        for corpo in [{}, {"papel": "ADMINISTRADOR"}, {"ativo": False}]:
            with self.subTest(corpo=corpo):
                self.assertEqual(self.patch(corpo).status_code, 400)

    def test_corpo_que_nao_e_json_objeto_e_400(self):
        for bruto in ["nao sou json", "[1, 2]", '"texto"']:
            with self.subTest(bruto=bruto):
                resposta = self.client.patch(
                    ME,
                    data=bruto,
                    content_type="application/json",
                    HTTP_AUTHORIZATION=f"Bearer {self.token_ana}",
                )
                self.assertEqual(resposta.status_code, 400)

    def test_sem_token_e_401(self):
        self.assertEqual(self.patch({"nome": "Ana Maria"}, token=None).status_code, 401)


class QuemEPermitidoAlterarTests(DadosPessoaisBase):
    def test_nao_altera_outro_usuario_nem_o_proprio_papel(self):
        resposta = self.patch(
            {
                "nome": "Ana Renomeada",
                "id": self.dados_bia["id"],
                "usuario_id": self.dados_bia["id"],
                "papel": "ADMINISTRADOR",
                "role": "ADMINISTRADOR",
                "role_code": "ADMINISTRADOR",
                "ativo": False,
            }
        )

        self.assertEqual(resposta.status_code, 200, resposta.content)
        ana, bia = self.usuario(), self.usuario(self.dados_bia)
        self.assertEqual(ana.nome, "Ana Renomeada")
        self.assertEqual(ana.papel_id, Papel.ENGENHARIA)
        self.assertTrue(ana.ativo)
        # A outra pessoa ficou intacta.
        self.assertEqual(bia.nome, "Bia Campos")
        self.assertEqual(bia.papel_id, Papel.QUALIDADE)
        self.assertTrue(bia.ativo)

    def test_o_id_vem_do_token_e_nao_do_corpo(self):
        self.patch({"nome": "Bia Trocada", "id": self.dados_bia["id"]})
        self.assertEqual(self.usuario(self.dados_bia).nome, "Bia Campos")
        self.assertEqual(self.usuario().nome, "Bia Trocada")


class TrocaDeEmailTests(DadosPessoaisBase):
    def test_email_so_muda_depois_de_confirmado(self):
        antigo = self.dados_ana["email"]

        token = self.pedir_troca()

        ana = self.usuario()
        self.assertEqual(ana.email, antigo)  # ainda o antigo
        self.assertEqual(ana.email_pendente, NOVO_EMAIL)
        self.assertEqual(self.login(antigo).status_code, 200)
        self.assertEqual(self.login(NOVO_EMAIL).status_code, 401)

        resposta = self.confirmar(token)

        self.assertEqual(resposta.status_code, 200, resposta.content)
        ana = self.usuario()
        self.assertEqual(ana.email, NOVO_EMAIL)
        self.assertIsNone(ana.email_pendente)
        self.assertIsNone(ana.token_confirmacao_hash)
        self.assertEqual(self.login(NOVO_EMAIL).status_code, 200)
        self.assertEqual(self.login(antigo).status_code, 401)

    def test_link_vai_para_o_email_novo_e_aviso_para_o_antigo(self):
        antigo = self.dados_ana["email"]
        token = self.pedir_troca()

        self.assertEqual(len(mail.outbox), 2)
        link = next(m for m in mail.outbox if m.to == [NOVO_EMAIL])
        aviso = next(m for m in mail.outbox if m.to == [antigo])
        self.assertIn(f"http://front.test/confirmar-email?token={token}", link.body)
        # O aviso do e-mail antigo não carrega o token nem o e-mail novo.
        self.assertNotIn(token, aviso.body)
        self.assertNotIn(NOVO_EMAIL, aviso.body)

    def test_token_usado_uma_vez_nao_vale_de_novo(self):
        token = self.pedir_troca()
        self.assertEqual(self.confirmar(token).status_code, 200)
        self.assertEqual(self.confirmar(token).status_code, 400)

    def test_token_invalido_e_400(self):
        self.pedir_troca()
        for token in ["token-que-nao-existe", "", None, 123]:
            with self.subTest(token=token):
                resposta = self.confirmar(token)
                self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertEqual(self.usuario().email_pendente, NOVO_EMAIL)

    def test_token_expirado_e_400(self):
        token = self.pedir_troca()
        Usuario.objects.using("credenciais_db").filter(pk=self.dados_ana["id"]).update(
            token_expira_em=timezone.now() - timedelta(seconds=1)
        )

        resposta = self.confirmar(token)

        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertEqual(self.usuario().email, self.dados_ana["email"])

    def test_token_vale_por_24_horas(self):
        self.pedir_troca()
        falta = self.usuario().token_expira_em - timezone.now()
        self.assertGreater(falta, timedelta(hours=23, minutes=59))
        self.assertLessEqual(falta, timedelta(hours=24))

    def test_so_o_hash_do_token_fica_no_banco(self):
        token = self.pedir_troca()
        guardado = self.usuario().token_confirmacao_hash
        self.assertNotEqual(guardado, token)
        self.assertEqual(guardado, hashlib.sha256(token.encode()).hexdigest())

    def test_email_duplicado_devolve_409_sem_diferenciar_maiusculas(self):
        existente = self.dados_bia["email"]
        for tentativa in [existente, existente.upper()]:
            with self.subTest(tentativa=tentativa):
                resposta = self.patch({"email": tentativa})
                self.assertEqual(resposta.status_code, 409, resposta.content)
                self.assertEqual(resposta.json()["field"], "email")
        self.assertIsNone(self.usuario().email_pendente)
        self.assertEqual(mail.outbox, [])

    def test_email_com_formato_invalido_e_400(self):
        for email in ["sem-arroba", "a@", "@b.com", "", "  ", "a b@c.com", None, 5]:
            with self.subTest(email=email):
                resposta = self.patch({"email": email})
                self.assertEqual(resposta.status_code, 400, resposta.content)
                self.assertEqual(resposta.json()["field"], "email")
        longo = "a" * 175 + "@x.com"
        self.assertEqual(self.patch({"email": longo}).status_code, 400)

    def test_email_e_guardado_em_minusculas(self):
        self.pedir_troca("Novo.Email@Akaer.com.br")
        self.assertEqual(self.usuario().email_pendente, "novo.email@akaer.com.br")

    def test_pedir_o_proprio_email_nao_faz_nada(self):
        resposta = self.patch({"email": self.dados_ana["email"].upper()})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertIsNone(self.usuario().email_pendente)
        self.assertEqual(mail.outbox, [])

    def test_pedir_o_proprio_email_cancela_a_troca_pendente(self):
        token = self.pedir_troca()
        resposta = self.patch({"email": self.dados_ana["email"]})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertIsNone(self.usuario().email_pendente)
        self.assertEqual(self.confirmar(token).status_code, 400)

    def test_email_ocupado_enquanto_o_link_esperava_e_409(self):
        token = self.pedir_troca()
        Usuario.objects.using("credenciais_db").filter(pk=self.dados_bia["id"]).update(
            email=NOVO_EMAIL
        )

        resposta = self.confirmar(token)

        self.assertEqual(resposta.status_code, 409, resposta.content)
        self.assertEqual(self.usuario().email, self.dados_ana["email"])
        self.assertIsNone(self.usuario().email_pendente)

    def test_nome_e_email_juntos(self):
        resposta = self.patch({"nome": "Ana Nova", "email": NOVO_EMAIL})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        ana = self.usuario()
        self.assertEqual((ana.nome, ana.email_pendente), ("Ana Nova", NOVO_EMAIL))

    def test_falha_no_envio_mantem_a_troca_pendente(self):
        with patch(
            "core_api.services.auth.dados_pessoais.send_mail",
            side_effect=OSError("smtp fora"),
        ):
            resposta = self.patch({"email": NOVO_EMAIL})
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual(self.usuario().email_pendente, NOVO_EMAIL)
        # E o botão "Reenviar link" resolve depois.
        self.liberar_intervalo()
        self.assertEqual(self.reenviar().status_code, 200)
        self.assertIn(NOVO_EMAIL, mail.outbox[-1].to)

    def test_get_me_devolve_email_pendente(self):
        def me():
            r = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {self.token_ana}")
            self.assertEqual(r.status_code, 200)
            return r.json()["user"]["email_pendente"]

        self.assertIsNone(me())
        self.pedir_troca()
        self.assertEqual(me(), NOVO_EMAIL)


class ReenvioDoLinkTests(DadosPessoaisBase):
    def test_reenviar_gera_token_novo_e_o_anterior_deixa_de_valer(self):
        antigo = self.pedir_troca()
        self.liberar_intervalo()

        resposta = self.reenviar()

        self.assertEqual(resposta.status_code, 200, resposta.content)
        novo = self.token_do_ultimo_link()
        self.assertNotEqual(novo, antigo)
        self.assertEqual(mail.outbox[-1].to, [NOVO_EMAIL])
        self.assertEqual(self.confirmar(antigo).status_code, 400)
        self.assertEqual(self.confirmar(novo).status_code, 200)
        self.assertEqual(self.usuario().email, NOVO_EMAIL)

    def test_reenviar_renova_a_validade(self):
        self.pedir_troca()
        Usuario.objects.using("credenciais_db").filter(pk=self.dados_ana["id"]).update(
            confirmacao_enviada_em=timezone.now() - timedelta(minutes=2),
            token_expira_em=timezone.now() + timedelta(minutes=5),
        )
        self.reenviar()
        falta = self.usuario().token_expira_em - timezone.now()
        self.assertGreater(falta, timedelta(hours=23))

    def test_sem_email_pendente_e_400(self):
        resposta = self.reenviar()
        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertEqual(mail.outbox, [])

    def test_mais_de_um_pedido_por_minuto_e_429(self):
        self.pedir_troca()
        total = len(mail.outbox)

        resposta = self.reenviar()

        self.assertEqual(resposta.status_code, 429, resposta.content)
        self.assertTrue(1 <= int(resposta["Retry-After"]) <= 60)
        self.assertEqual(len(mail.outbox), total)
        self.liberar_intervalo()
        self.assertEqual(self.reenviar().status_code, 200)
        self.assertEqual(self.reenviar().status_code, 429)

    def test_nova_troca_dentro_de_um_minuto_tambem_e_429(self):
        token = self.pedir_troca()
        resposta = self.patch({"email": "outro.endereco@akaer.com.br"})
        self.assertEqual(resposta.status_code, 429, resposta.content)
        # Nada mudou: o pedido anterior continua valendo.
        self.assertEqual(self.usuario().email_pendente, NOVO_EMAIL)
        self.assertEqual(self.confirmar(token).status_code, 200)

    def test_nova_troca_depois_do_intervalo_invalida_o_link_anterior(self):
        antigo = self.pedir_troca()
        self.liberar_intervalo()
        novo = self.pedir_troca("outro.endereco@akaer.com.br")
        self.assertEqual(self.confirmar(antigo).status_code, 400)
        self.assertEqual(self.confirmar(novo).status_code, 200)
        self.assertEqual(self.usuario().email, "outro.endereco@akaer.com.br")

    def test_so_o_dono_reenvia_o_proprio_link(self):
        self.pedir_troca()
        self.liberar_intervalo()
        # Bia não tem troca pendente, mesmo com a da Ana existindo.
        self.assertEqual(self.reenviar(token=self.token_bia).status_code, 400)

    def test_sem_token_e_401(self):
        self.assertEqual(self.reenviar(token=None).status_code, 401)


class LogDeAuditoriaTests(DadosPessoaisBase):
    def test_log_registra_usuario_campos_e_data_sem_valores(self):
        with self.assertLogs("core_api.auditoria", level="INFO") as capturado:
            self.patch({"nome": "Nome Secreto Novo", "email": NOVO_EMAIL})

        saida = "\n".join(capturado.output)
        self.assertIn("DADOS_PESSOAIS_ALTERADOS", saida)
        self.assertIn(self.dados_ana["id"], saida)
        self.assertIn("nome", saida)
        self.assertIn("email_pendente", saida)
        self.assertRegex(saida, r"data=\d{4}-\d{2}-\d{2}T")
        for valor in ["Nome Secreto Novo", NOVO_EMAIL, self.dados_ana["email"]]:
            self.assertNotIn(valor, saida)

    def test_confirmacao_tambem_e_registrada_sem_valores(self):
        token = self.pedir_troca()
        with self.assertLogs("core_api.auditoria", level="INFO") as capturado:
            self.confirmar(token)
        saida = "\n".join(capturado.output)
        self.assertIn("DADOS_PESSOAIS_ALTERADOS", saida)
        self.assertIn("campos=email", saida)
        self.assertNotIn(NOVO_EMAIL, saida)
        self.assertNotIn(token, saida)

    def test_sem_alteracao_nao_gera_log(self):
        with self.assertNoLogs("core_api.auditoria", level="INFO"):
            self.patch({"nome": "Ana Souza"})  # mesmo nome de antes
