"""
Gravação do log de auditoria (AKA-31).

    registrar_acao(usuario_id, acao, entidade, registro_id, campos)
        para ações sem model próprio: LOGIN, LOGIN_FALHOU e, a partir do
        AKA-57, DADOS_PESSOAIS_ALTERADOS. As mudanças em Documento,
        DocumentoArea e HistoricoValidacao NÃO passam por aqui à mão: os
        signals de core_api/signals.py chamam esta função sozinhos.

A visualização de documento vai para LogAcesso, gravada uma vez só por
services/visualizacao.registrar_visualizacao (GET .../visualizar/).

`acao` e `entidade` precisam estar nas listas fixas, e `campos` só aceita
nomes de campo: um valor fora disso levanta ValueError em vez de gravar
qualquer coisa no log.
"""

from core_api.models import (
    AcaoAuditoria,
    EntidadeAuditoria,
    LogAuditoria,
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
