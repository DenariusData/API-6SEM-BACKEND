"""
Dados pessoais do próprio usuário (S2-49): nome e e-mail.

    atualizar(usuario_id, dados)      PATCH /api/auth/me/
    confirmar_email(token)            POST  /api/auth/confirmar-email/
    reenviar_confirmacao(usuario_id)  POST  /api/auth/reenviar-confirmacao/

Regras:
  - o id vem SEMPRE do token (quem chama passa `usuario_id`); `dados` só
    é lido para `nome` e `email`, qualquer outra chave (papel, ativo, id...)
    é ignorada;
  - nome: de 3 a 150 caracteres. E-mail: formato válido, até 180 caracteres e
    único sem diferenciar maiúsculas (EmailEmUso, HTTP 409);
  - trocar o e-mail NÃO troca o e-mail: grava `email_pendente` e um token de
    24 h. O link vai para o e-mail novo, um aviso vai para o antigo. Até a
    confirmação o login continua com o e-mail antigo;
  - só o SHA-256 do token fica no banco. Um token por usuário: gerar outro
    sobrescreve o hash e o anterior deixa de valer;
  - no máximo 1 envio de link por minuto por usuário (MuitosPedidos, 429),
    valendo para a troca de e-mail e para o reenvio;
  - a alteração é gravada no log `core_api.auditoria` como
    DADOS_PESSOAIS_ALTERADOS com usuário, campos e data, SEM os valores;
  - tudo em `credenciais_db`, com o usuário travado (`select_for_update`).
    Os e-mails saem DEPOIS do commit; se o envio falhar, a troca continua
    pendente e o usuário pode pedir o reenvio.
"""

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from credenciais.models import Usuario

BANCO = "credenciais_db"
NOME_MINIMO = 3
NOME_MAXIMO = 150
EMAIL_MAXIMO = 180
VALIDADE_TOKEN = timedelta(hours=24)
INTERVALO_ENVIO = timedelta(minutes=1)

auditoria = logging.getLogger("core_api.auditoria")
logger = logging.getLogger(__name__)


class DadosInvalidos(Exception):
    """Campo inválido (HTTP 400). `campo` diz qual (None = requisição inteira)."""

    def __init__(self, mensagem, campo=None):
        super().__init__(mensagem)
        self.campo = campo


class EmailEmUso(Exception):
    """Já existe outro usuário com este e-mail (HTTP 409)."""


class TokenInvalido(Exception):
    """Token de confirmação inexistente, usado ou expirado (HTTP 400)."""


class SemEmailPendente(Exception):
    """Não há troca de e-mail aguardando confirmação (HTTP 400)."""


class MuitosPedidos(Exception):
    """Mais de 1 envio de link por minuto (HTTP 429)."""

    def __init__(self, mensagem, segundos):
        super().__init__(mensagem)
        self.segundos = segundos


# -- validação ------------------------------------------------------------
def _nome(valor) -> str:
    nome = valor.strip() if isinstance(valor, str) else ""
    if not NOME_MINIMO <= len(nome) <= NOME_MAXIMO:
        raise DadosInvalidos(
            f"O nome deve ter entre {NOME_MINIMO} e {NOME_MAXIMO} caracteres.", "nome"
        )
    return nome


def _email(valor) -> str:
    email = valor.strip().lower() if isinstance(valor, str) else ""
    try:
        if not email or len(email) > EMAIL_MAXIMO:
            raise ValidationError("tamanho")
        validate_email(email)
    except ValidationError:
        raise DadosInvalidos("Informe um e-mail válido.", "email")
    return email


def _em_uso(email, excluir_id) -> bool:
    return (
        Usuario.objects.using(BANCO)
        .filter(email__iexact=email)
        .exclude(pk=excluir_id)
        .exists()
    )


# -- token e e-mails ------------------------------------------------------
def _hash(token) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _exigir_intervalo(usuario) -> None:
    enviado = usuario.confirmacao_enviada_em
    if enviado and timezone.now() - enviado < INTERVALO_ENVIO:
        faltam = INTERVALO_ENVIO - (timezone.now() - enviado)
        segundos = int(faltam.total_seconds()) + 1
        raise MuitosPedidos(
            f"Aguarde {segundos} segundos para pedir um novo link.", segundos
        )


def _novo_token(usuario) -> str:
    """Gera um token, grava o hash e a expiração (substitui o anterior). Quem
    chama salva o usuário."""
    token = secrets.token_urlsafe(32)
    agora = timezone.now()
    usuario.token_confirmacao_hash = _hash(token)
    usuario.token_expira_em = agora + VALIDADE_TOKEN
    usuario.confirmacao_enviada_em = agora
    return token


def _limpar_pendencia(usuario) -> None:
    usuario.email_pendente = None
    usuario.token_confirmacao_hash = None
    usuario.token_expira_em = None


def _enviar_link(nome, email_novo, token) -> bool:
    link = f"{settings.FRONTEND_URL.rstrip('/')}/confirmar-email?token={token}"
    try:
        send_mail(
            "Confirme seu novo e-mail",
            f"Olá, {nome}.\n\nPara confirmar a troca do seu e-mail de acesso, abra o "
            f"link abaixo em até 24 horas:\n\n{link}\n\nSe não foi você, ignore "
            "esta mensagem: o e-mail atual continua valendo.",
            settings.DEFAULT_FROM_EMAIL,
            [email_novo],
        )
    except Exception:
        logger.exception("Falha ao enviar o link de confirmação de e-mail")
        return False
    return True


def _avisar_antigo(nome, email_antigo) -> None:
    try:
        send_mail(
            "Solicitação de alteração do seu e-mail",
            f"Olá, {nome}.\n\nFoi solicitada a troca do e-mail da sua conta. O "
            "e-mail atual continua valendo até que o novo seja confirmado. Se "
            "não foi você, avise o administrador e troque sua senha.",
            settings.DEFAULT_FROM_EMAIL,
            [email_antigo],
        )
    except Exception:
        logger.exception("Falha ao enviar o aviso de alteração de e-mail")


def _registrar(usuario_id, campos) -> None:
    """Auditoria: quem, quais campos e quando. Nunca os valores."""
    auditoria.info(
        "DADOS_PESSOAIS_ALTERADOS usuario=%s campos=%s data=%s",
        usuario_id,
        ",".join(campos),
        timezone.now().isoformat(),
    )


# -- operações ------------------------------------------------------------
def atualizar(usuario_id, dados):
    """
    Altera nome e/ou pede a troca de e-mail do usuário `usuario_id` (o `sub`
    do JWT). Devolve (usuario, confirmacao_enviada), onde `confirmacao_enviada`
    é None se não houve pedido de troca, e True/False se o link saiu ou não.

    Levanta DadosInvalidos, EmailEmUso, MuitosPedidos ou
    Usuario.DoesNotExist.
    """
    nome = _nome(dados["nome"]) if "nome" in dados else None
    email = _email(dados["email"]) if "email" in dados else None
    if nome is None and email is None:
        raise DadosInvalidos("Informe o nome e/ou o e-mail.")

    token = None
    alterados = []
    try:
        with transaction.atomic(using=BANCO):
            usuario = (
                Usuario.objects.using(BANCO)
                .select_for_update()
                .select_related("papel")
                .get(pk=usuario_id)
            )
            campos = []
            if nome is not None and nome != usuario.nome:
                usuario.nome = nome
                campos.append("nome")
            if email is not None:
                if email == usuario.email.lower():
                    # Pedir o e-mail que já é o seu cancela a troca pendente.
                    if usuario.email_pendente:
                        _limpar_pendencia(usuario)
                        campos.append("email_pendente")
                else:
                    if _em_uso(email, usuario.pk):
                        raise EmailEmUso("Este e-mail já está em uso.")
                    _exigir_intervalo(usuario)
                    usuario.email_pendente = email
                    token = _novo_token(usuario)
                    campos.append("email_pendente")
            if campos:
                extras = []
                if "email_pendente" in campos:
                    extras += ["token_confirmacao_hash", "token_expira_em"]
                if token is not None:
                    extras.append("confirmacao_enviada_em")
                usuario.save(using=BANCO, update_fields=campos + extras)
            alterados = campos
    except IntegrityError as erro:
        raise EmailEmUso("Este e-mail já está em uso.") from erro

    enviada = None
    if token is not None:
        enviada = _enviar_link(usuario.nome, usuario.email_pendente, token)
        _avisar_antigo(usuario.nome, usuario.email)
    if alterados:
        _registrar(usuario.pk, alterados)
    return usuario, enviada


def confirmar_email(token) -> Usuario:
    """Troca o e-mail do dono do token pelo e-mail pendente e consome o token.
    Levanta TokenInvalido (inexistente, usado ou expirado) ou EmailEmUso (o
    e-mail foi ocupado por outro usuário enquanto o link esperava)."""
    if not isinstance(token, str) or not token.strip():
        raise TokenInvalido("Token de confirmação inválido ou expirado.")

    conflito = False
    with transaction.atomic(using=BANCO):
        usuario = (
            Usuario.objects.using(BANCO)
            .select_for_update()
            .filter(token_confirmacao_hash=_hash(token.strip()))
            .first()
        )
        if (
            usuario is None
            or not usuario.email_pendente
            or usuario.token_expira_em is None
            or usuario.token_expira_em < timezone.now()
        ):
            raise TokenInvalido("Token de confirmação inválido ou expirado.")

        if _em_uso(usuario.email_pendente, usuario.pk):
            conflito = True
            campos = ["email_pendente", "token_confirmacao_hash", "token_expira_em"]
            _limpar_pendencia(usuario)
        else:
            usuario.email = usuario.email_pendente
            _limpar_pendencia(usuario)
            campos = [
                "email",
                "email_pendente",
                "token_confirmacao_hash",
                "token_expira_em",
            ]
        try:
            usuario.save(using=BANCO, update_fields=campos)
        except IntegrityError:
            raise EmailEmUso("Este e-mail já está em uso.")
    if conflito:
        raise EmailEmUso("Este e-mail já está em uso.")
    _registrar(usuario.pk, ["email"])
    return usuario


def reenviar_confirmacao(usuario_id) -> bool:
    """Gera um token novo (o anterior deixa de valer) e reenvia o link.
    Devolve se o e-mail saiu. Levanta SemEmailPendente ou MuitosPedidos."""
    with transaction.atomic(using=BANCO):
        usuario = Usuario.objects.using(BANCO).select_for_update().get(pk=usuario_id)
        if not usuario.email_pendente:
            raise SemEmailPendente("Não há troca de e-mail aguardando confirmação.")
        _exigir_intervalo(usuario)
        token = _novo_token(usuario)
        usuario.save(
            using=BANCO,
            update_fields=[
                "token_confirmacao_hash",
                "token_expira_em",
                "confirmacao_enviada_em",
            ],
        )
    return _enviar_link(usuario.nome, usuario.email_pendente, token)
