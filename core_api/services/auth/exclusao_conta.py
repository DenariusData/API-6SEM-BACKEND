"""
Exclusão física da conta (AKA-110, LGPD art. 18, VI).

A linha do usuário em `credenciais_db.usuario` é apagada de verdade
(`.delete()`), não só marcada como inativa. Como o banco de negócio guarda só
o uuid, o que fica lá continua íntegro, mas deixa de identificar alguém:

- **Mantidos:** LogAuditoria, LogAcesso (imutáveis por trigger, AKA-80),
  HistoricoValidacao e documentos. Onde o sistema mostrava o nome, passa a
  mostrar "Usuário removido".
- **Apagados:** as perguntas feitas à IA (o texto pode conter dado pessoal) e
  as permissões de área.
- **Descaracterizado:** o PerfilOperacional fica (documentos apontam para ele),
  mas sem matrícula, cargo e avatar do titular.

A própria exclusão vai para o log de auditoria, só com os ids.

Limite conhecido: um token já emitido para o usuário excluído continua
válido até expirar (JWT_EXP_MINUTES). As rotas que leem o usuário respondem
404, e o login deixa de funcionar na hora.
"""

from django.db import transaction

from core_api.models import (
    AcaoAuditoria,
    EntidadeAuditoria,
    PerfilOperacional,
    RegistroPergunta,
    UsuarioPermissaoArea,
)
from core_api.services.auditoria import registrar_acao
from credenciais.models import Usuario

AVATAR_PADRAO = "/avatars/default.png"


class UsuarioNaoEncontrado(Exception):
    pass


class SenhaIncorreta(Exception):
    pass


def _matricula_removida(usuario_id) -> str:
    # Única (a coluna é unique) e sem nada do titular: "REMOVIDO-" + 8 do uuid.
    return f"REMOVIDO-{str(usuario_id).replace('-', '')[:8]}"


def confirmar_senha(usuario_id, senha) -> None:
    """Para a exclusão pelo próprio titular: a senha confirma a intenção."""
    usuario = Usuario.objects.using("credenciais_db").filter(id=usuario_id).first()
    if usuario is None:
        raise UsuarioNaoEncontrado()
    if not isinstance(senha, str) or not usuario.verificar_senha(senha):
        raise SenhaIncorreta()


def excluir_conta(usuario_id, excluido_por_id) -> None:
    """Apaga fisicamente o usuário `usuario_id`. `excluido_por_id` é quem pediu
    (ele mesmo ou um administrador) e vai para o log."""
    usuario = Usuario.objects.using("credenciais_db").filter(id=usuario_id).first()
    if usuario is None:
        raise UsuarioNaoEncontrado()

    with (
        transaction.atomic(using="default"),
        transaction.atomic(using="credenciais_db"),
    ):
        # Perguntas (e suas fontes, em cascata) e permissões do titular.
        RegistroPergunta.objects.filter(usuario_id=usuario.id).delete()
        UsuarioPermissaoArea.objects.filter(usuario_id=usuario.id).delete()
        PerfilOperacional.objects.filter(usuario_id=usuario.id).update(
            matricula=_matricula_removida(usuario.id),
            cargo="",
            avatar=AVATAR_PADRAO,
            allowed_menus=[],
        )
        usuario.delete(using="credenciais_db")
        registrar_acao(
            excluido_por_id,
            AcaoAuditoria.EXCLUIDO,
            EntidadeAuditoria.USUARIO,
            usuario_id,
        )
