"""
Alertas de segurança por e-mail (AKA-78).

Avisa os administradores (e o e-mail opcional `ALERTA_EMAIL`) quando algo
foge do normal, para a empresa saber que pode haver vazamento ou sequestro de
dados:

- LOGIN_BLOQUEADO: um usuário foi bloqueado por tentativas de senha erradas;
- ACESSOS_EM_MASSA: um usuário passou de N visualizações em X minutos
  (no máximo 1 alerta por usuário por janela);
- ARQUIVOS_ALTERADOS: muitos arquivos da pasta de uploads mudaram ou sumiram
  de uma vez (comando `verificar_integridade_arquivos`).

O e-mail leva só o tipo, o horário e o id do usuário: nada de nome ou e-mail
de terceiros (LGPD). Cada alerta também vai para o log de auditoria, com a
ação ALERTA_SEGURANCA.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    EntidadeAuditoria,
    LogAcesso,
    LogAuditoria,
)
from core_api.services.auditoria import registrar_acao
from credenciais.models import Papel, Usuario

logger = logging.getLogger(__name__)

LOGIN_BLOQUEADO = "LOGIN_BLOQUEADO"
ACESSOS_EM_MASSA = "ACESSOS_EM_MASSA"
ARQUIVOS_ALTERADOS = "ARQUIVOS_ALTERADOS"

DESCRICOES = {
    LOGIN_BLOQUEADO: "Usuário bloqueado por excesso de tentativas de senha.",
    ACESSOS_EM_MASSA: "Visualizações de documentos acima do limite em pouco tempo.",
    ARQUIVOS_ALTERADOS: "Muitos arquivos alterados ou removidos de uma vez.",
}


def destinatarios() -> list[str]:
    emails = list(
        Usuario.objects.using("credenciais_db")
        .filter(papel_id=Papel.ADMINISTRADOR, ativo=True)
        .values_list("email", flat=True)
    )
    extra = (getattr(settings, "ALERTA_EMAIL", "") or "").strip()
    if extra and extra not in emails:
        emails.append(extra)
    return emails


def _corpo(tipo, usuario_id, detalhes, quando) -> str:
    linhas = [
        f"Alerta de segurança do AkaVision: {tipo}",
        DESCRICOES.get(tipo, ""),
        "",
        f"Horário: {timezone.localtime(quando):%d/%m/%Y %H:%M:%S}",
    ]
    if usuario_id:
        linhas.append(f"Id do usuário: {usuario_id}")
    for chave, valor in (detalhes or {}).items():
        linhas.append(f"{chave}: {valor}")
    linhas += ["", "Consulte a tela de Auditoria para os detalhes."]
    return "\n".join(linhas)


def enviar_alerta(tipo, usuario_id=None, detalhes=None) -> None:
    """Manda o e-mail e grava o alerta no log de auditoria. Falha no envio do
    e-mail não interrompe a operação que disparou o alerta."""
    quando = timezone.now()
    registrar_acao(
        None,
        AcaoAuditoria.ALERTA_SEGURANCA,
        EntidadeAuditoria.USUARIO,
        str(usuario_id) if usuario_id else None,
        campos=[tipo],
    )
    para = destinatarios()
    if not para:
        logger.warning("Alerta %s sem destinatário (nenhum administrador ativo).", tipo)
        return
    try:
        send_mail(
            subject=f"[AkaVision] Alerta de segurança: {tipo}",
            message=_corpo(tipo, usuario_id, detalhes, quando),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=para,
        )
    except Exception:  # noqa: BLE001 - o alerta não pode derrubar a requisição
        logger.exception("Falha ao enviar o e-mail do alerta %s.", tipo)


def verificar_acessos(usuario_id) -> bool:
    """Depois de gravar uma visualização: se o usuário passou do limite na
    janela, avisa (uma vez por janela). Devolve True se alertou."""
    janela = timezone.timedelta(minutes=settings.ALERTA_ACESSOS_JANELA_MIN)
    inicio = timezone.now() - janela
    acessos = LogAcesso.objects.filter(
        usuario_id=usuario_id,
        acao=AcaoAcesso.VISUALIZACAO,
        criado_em__gte=inicio,
    ).count()
    if acessos <= settings.ALERTA_ACESSOS_LIMITE:
        return False
    ja_avisado = LogAuditoria.objects.filter(
        acao=AcaoAuditoria.ALERTA_SEGURANCA,
        registro_id=str(usuario_id),
        campos_alterados__contains=[ACESSOS_EM_MASSA],
        criado_em__gte=inicio,
    ).exists()
    if ja_avisado:
        return False
    enviar_alerta(
        ACESSOS_EM_MASSA,
        usuario_id,
        {
            "Visualizações na janela": acessos,
            "Janela (minutos)": settings.ALERTA_ACESSOS_JANELA_MIN,
        },
    )
    return True
