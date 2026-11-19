"""
Existência mínima (decisão D11): quem não tem permissão para um documento
vê SÓ a mensagem de acesso não autorizado.

O corpo da resposta é fixo e não carrega nada do documento: nem título,
código, área, nível de sigilo, status, resumo, trecho ou conteúdo. Por isso
ele é montado aqui, sem receber o documento, e a resposta é igual para
qualquer documento e para qualquer motivo da negativa (sem permissão na
área, sigilo acima do máximo, documento ainda em validação de outra pessoa).

`pode_solicitar_acesso` é o gancho do botão "pedir acesso" (AKA-72): o front
mostra o botão quando `codigo == ACESSO_NAO_AUTORIZADO`.

Documento que não existe continua 404. Quem pede um id que existe e não pode
ver recebe 403 (é o que permite oferecer o pedido de acesso); o id é um uuid
v4, então não dá para descobrir documentos varrendo ids.
"""

CODIGO = "ACESSO_NAO_AUTORIZADO"
MENSAGEM = "Acesso não autorizado."


class AcessoNaoAutorizado(Exception):
    """O usuário existe e o documento existe, mas ele não pode vê-lo (403)."""


def corpo() -> dict:
    return {
        "detail": MENSAGEM,
        "codigo": CODIGO,
        "pode_solicitar_acesso": True,
    }
