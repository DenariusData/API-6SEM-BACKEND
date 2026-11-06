"""
Gravação automática do log de auditoria (AKA-31).

    Documento, DocumentoArea    criado / atualizado / excluído
    HistoricoValidacao          ENVIADO, DEVOLVIDO, REENVIADO, APROVADO

Nenhuma tela precisa chamar o log: qualquer save()/delete() desses models,
venha de onde vier, grava a linha. O usuário vem do token da requisição
(core_api.utils.usuario_atual). Fora de requisição, o histórico usa o próprio
`usuario` da linha; os demais ficam sem usuário.

Atualização grava só os NOMES dos campos que mudaram. Para saber o que mudou,
`post_init` guarda os valores carregados do banco no próprio objeto (só em
memória, nunca no log) e `post_save` compara. Salvar sem mudar nada não gera
linha.

A linha do log é gravada na mesma transação da ação: se a ação for desfeita
(rollback), o log dela também é.

Limite conhecido: QuerySet.update(), bulk_update() e delete() em massa não
disparam signals do Django, então não passam por aqui. O código da aplicação
muda estado com save() (services/validacao.py); mantenha assim.
"""

from django.db.models.signals import post_delete, post_init, post_save
from django.dispatch import receiver

from core_api.models import (
    AcaoAuditoria,
    Documento,
    DocumentoArea,
    EntidadeAuditoria,
    HistoricoValidacao,
)
from core_api.services.auditoria import registrar_acao
from core_api.utils.usuario_atual import get_usuario_atual

ENTIDADES = {
    Documento: EntidadeAuditoria.DOCUMENTO,
    DocumentoArea: EntidadeAuditoria.DOCUMENTO_AREA,
}
# Datas automáticas mudam a cada save; não são uma alteração feita por alguém.
CAMPOS_IGNORADOS = {"criado_em", "atualizado_em"}


def _valores(instance):
    """Valores dos campos já carregados (campo adiado com .only() fica de
    fora, para não disparar consulta só por causa do log)."""
    carregados = instance.__dict__
    return {
        f.attname: carregados[f.attname]
        for f in instance._meta.concrete_fields
        if f.attname in carregados
    }


def _campos_alterados(instance, update_fields):
    antes = getattr(instance, "_auditoria_valores", {})
    depois = _valores(instance)
    alterados = []
    for campo in instance._meta.concrete_fields:
        if campo.name in CAMPOS_IGNORADOS or campo.primary_key:
            continue
        if update_fields is not None and campo.name not in update_fields:
            continue
        if campo.attname not in antes:
            # Valor original desconhecido (campo adiado): só conta se o save
            # disse explicitamente que gravou este campo.
            if update_fields is not None:
                alterados.append(campo.name)
            continue
        if antes[campo.attname] != depois.get(campo.attname):
            alterados.append(campo.name)
    return alterados


def _guardar_valores(sender, instance, **kwargs):
    instance._auditoria_valores = _valores(instance)


def _registrar_save(sender, instance, created, update_fields=None, **kwargs):
    if kwargs.get("raw"):  # loaddata/fixtures
        return
    if created:
        acao, campos = AcaoAuditoria.CRIADO, []
    else:
        campos = _campos_alterados(instance, update_fields)
        if not campos:
            return
        acao = AcaoAuditoria.ATUALIZADO
    registrar_acao(get_usuario_atual(), acao, ENTIDADES[sender], instance.pk, campos)
    _guardar_valores(sender, instance)


def _registrar_delete(sender, instance, **kwargs):
    registrar_acao(
        get_usuario_atual(), AcaoAuditoria.EXCLUIDO, ENTIDADES[sender], instance.pk
    )


for _model in ENTIDADES:
    _uid = f"auditoria_{_model.__name__}"
    post_init.connect(_guardar_valores, sender=_model, dispatch_uid=f"{_uid}_init")
    post_save.connect(_registrar_save, sender=_model, dispatch_uid=f"{_uid}_save")
    post_delete.connect(_registrar_delete, sender=_model, dispatch_uid=f"{_uid}_del")


@receiver(post_save, sender=HistoricoValidacao, dispatch_uid="aud_historico")
def registrar_historico(sender, instance, created, **kwargs):
    """Cada ação do fluxo de validação vira uma linha ligada ao histórico:
    a ação é a do histórico e o registro é o id da linha do histórico. O
    comentário fica só no histórico (o histórico é só inserção, então não
    existe atualização para registrar)."""
    if not created or kwargs.get("raw"):
        return
    registrar_acao(
        get_usuario_atual() or instance.usuario_id,
        instance.acao,
        EntidadeAuditoria.HISTORICO_VALIDACAO,
        instance.pk,
    )
