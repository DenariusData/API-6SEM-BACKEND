"""
Gravação do log de auditoria (AKA-31).

    registrar_acao(usuario_id, acao, entidade, registro_id, campos)
        para ações sem model próprio: LOGIN, LOGIN_FALHOU e, a partir do
        AKA-57, DADOS_PESSOAIS_ALTERADOS. As mudanças em Documento,
        DocumentoArea e HistoricoValidacao NÃO passam por aqui à mão: os
        signals de core_api/signals.py chamam esta função sozinhos.

    registrar_visualizacao(documento_id, usuario_id)
        visualização de documento, que vai para LogAcesso (não para o log de
        auditoria).

`acao` e `entidade` precisam estar nas listas fixas, e `campos` só aceita
nomes de campo: um valor fora disso levanta ValueError em vez de gravar
qualquer coisa no log.
"""

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    EntidadeAuditoria,
    LogAcesso,
    LogAuditoria,
    PerfilOperacional,
)


def registrar_acao(usuario_id, acao, entidade, registro_id=None, campos=None):
    if acao not in AcaoAuditoria.values:
        raise ValueError(f"Ação de auditoria inválida: {acao!r}.")
    if entidade not in EntidadeAuditoria.values:
        raise ValueError(f"Entidade de auditoria inválida: {entidade!r}.")
    campos = list(campos or [])
    if not all(isinstance(c, str) and c.isidentifier() for c in campos):
        raise ValueError("campos_alterados aceita só nomes de campo.")

    return LogAuditoria.objects.create(
        usuario_id=usuario_id,
        acao=acao,
        entidade=entidade,
        registro_id=str(registro_id) if registro_id is not None else None,
        campos_alterados=campos,
    )


def registrar_visualizacao(documento_id, usuario_id):
    """Grava a visualização em LogAcesso. Usuário sem perfil operacional não
    tem como ser referenciado (FK), então fica sem registro."""
    if usuario_id is None:
        return None
    if not PerfilOperacional.objects.filter(pk=usuario_id).exists():
        return None
    return LogAcesso.objects.create(
        documento_id=documento_id,
        usuario_id=usuario_id,
        acao=AcaoAcesso.VISUALIZACAO,
    )
