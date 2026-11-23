"""Alertas de segurança por e-mail (AKA-78).

Os testes usam o backend de e-mail em memória do Django (locmem): o que seria
enviado fica em `mail.outbox`.
"""

import json

from django.core import mail
from django.test import Client, TestCase, override_settings

from core_api.management.commands import verificar_integridade_arquivos
from core_api.models import AcaoAuditoria, LogAuditoria
from core_api.services import alertas
from core_api.services.auth.autenticacao import MAX_TENTATIVAS_LOGIN, AuthService
from core_api.services.visualizacao import registrar_visualizacao
from credenciais.models import Papel
from tests.integration.test_processamento import ProcessamentoBase


def _alertas(tipo):
    return LogAuditoria.objects.filter(
        acao=AcaoAuditoria.ALERTA_SEGURANCA, campos_alterados__contains=[tipo]
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LoginBloqueadoTests(TestCase):
    databases = {"default", "credenciais_db"}

    @classmethod
    def setUpTestData(cls):
        AuthService.create_user(
            {
                "email": "admin@akaer.com.br",
                "password": "admin123",
                "name": "Ricardo Mendes",
                "role": Papel.ADMINISTRADOR,
            }
        )
        cls.eng = AuthService.create_user(
            {
                "email": "eng@akaer.com.br",
                "password": "eng123",
                "name": "Carlos Eduardo",
                "role": Papel.ENGENHARIA,
            }
        )

    def tentar(self, senha):
        return Client().post(
            "/api/auth/login/",
            data=json.dumps({"email": "eng@akaer.com.br", "password": senha}),
            content_type="application/json",
        )

    def test_bloqueio_de_login_avisa_o_admin_uma_vez(self):
        for _ in range(MAX_TENTATIVAS_LOGIN + 2):
            self.tentar("errada")

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["admin@akaer.com.br"])
        self.assertIn(alertas.LOGIN_BLOQUEADO, email.subject)
        self.assertIn(self.eng["id"], email.body)
        self.assertEqual(_alertas(alertas.LOGIN_BLOQUEADO).count(), 1)

    def test_email_nao_leva_nome_nem_email_do_usuario(self):
        for _ in range(MAX_TENTATIVAS_LOGIN):
            self.tentar("errada")

        corpo = mail.outbox[0].body + mail.outbox[0].subject
        self.assertNotIn("Carlos", corpo)
        self.assertNotIn("eng@akaer.com.br", corpo)

    @override_settings(ALERTA_EMAIL="seguranca@akaer.com.br")
    def test_email_extra_tambem_recebe(self):
        for _ in range(MAX_TENTATIVAS_LOGIN):
            self.tentar("errada")

        self.assertIn("seguranca@akaer.com.br", mail.outbox[0].to)

    def test_senha_errada_sem_bloqueio_nao_avisa(self):
        self.tentar("errada")

        self.assertEqual(mail.outbox, [])


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ALERTA_EMAIL="seguranca@akaer.com.br",
    ALERTA_ACESSOS_LIMITE=3,
    ALERTA_ACESSOS_JANELA_MIN=10,
    ALERTA_INTEGRIDADE_PERCENTUAL=20,
    PROCESSAMENTO_AUTOMATICO=False,
)
class AcessosEIntegridadeTests(ProcessamentoBase):
    def test_acessos_acima_do_limite_geram_um_unico_alerta_por_janela(self):
        doc = self.documento()
        for _ in range(8):
            registrar_visualizacao(doc, self.autor.pk)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(alertas.ACESSOS_EM_MASSA, mail.outbox[0].subject)
        self.assertIn(str(self.autor.pk), mail.outbox[0].body)
        self.assertEqual(_alertas(alertas.ACESSOS_EM_MASSA).count(), 1)

    def test_acessos_dentro_do_limite_nao_avisam(self):
        doc = self.documento()
        for _ in range(3):
            registrar_visualizacao(doc, self.autor.pk)

        self.assertEqual(mail.outbox, [])

    def test_arquivos_alterados_em_massa_geram_alerta(self):
        docs = [self.documento() for _ in range(4)]
        primeira = verificar_integridade_arquivos.verificar()
        self.assertFalse(primeira["alertou"])  # só grava a referência

        # "Ransomware": 2 de 4 arquivos (50%) criptografados.
        for doc in docs[:2]:
            (self.pasta / doc.arquivo_original_url).write_bytes(b"cifrado" * 100)

        resumo = verificar_integridade_arquivos.verificar()

        self.assertTrue(resumo["alertou"])
        self.assertEqual(resumo["alterados"], 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(alertas.ARQUIVOS_ALTERADOS, mail.outbox[0].subject)
        self.assertEqual(_alertas(alertas.ARQUIVOS_ALTERADOS).count(), 1)

    def test_arquivos_apagados_contam_como_afetados(self):
        docs = [self.documento() for _ in range(3)]
        verificar_integridade_arquivos.verificar()
        (self.pasta / docs[0].arquivo_original_url).unlink()

        resumo = verificar_integridade_arquivos.verificar()

        self.assertEqual(resumo["sumidos"], 1)
        self.assertTrue(resumo["alertou"])

    def test_poucas_mudancas_nao_avisam(self):
        docs = [self.documento() for _ in range(10)]
        verificar_integridade_arquivos.verificar()
        (self.pasta / docs[0].arquivo_original_url).write_bytes(b"nova versao")

        resumo = verificar_integridade_arquivos.verificar()

        self.assertEqual(resumo["alterados"], 1)  # 10%, abaixo de 20%
        self.assertFalse(resumo["alertou"])
        self.assertEqual(mail.outbox, [])

    def test_sem_administrador_o_alerta_ainda_vai_para_o_log(self):
        with override_settings(ALERTA_EMAIL=""):
            alertas.enviar_alerta(alertas.ARQUIVOS_ALTERADOS, None, {"x": 1})

        self.assertEqual(mail.outbox, [])
        self.assertEqual(_alertas(alertas.ARQUIVOS_ALTERADOS).count(), 1)
