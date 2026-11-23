"""
Termos e consentimento (AKA-109).

Regras:
- Um termo está pendente para o usuário quando ele ainda não respondeu à
  versão ativa (nova versão = novo registro, então volta a ficar pendente).
- Obrigatório só pode ser aceito: recusar um obrigatório é sair do sistema,
  o que o front faz voltando ao login.
- Opcional pode ser aceito, recusado e revogado depois, sem bloquear o acesso.
- Toda resposta é uma linha nova em AceiteTermo (vale a mais recente) e vai
  para o log de auditoria. O cadastro de termo também.
"""

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery

from core_api.models import (
    AcaoAuditoria,
    AceiteTermo,
    EntidadeAuditoria,
    Termo,
)
from core_api.services.auditoria import registrar_acao

TITULO_MAXIMO = 150
VERSAO_MAXIMA = 20


class DadosInvalidos(ValueError):
    def __init__(self, mensagem, campo=None):
        super().__init__(mensagem)
        self.campo = campo


def _ativos():
    return Termo.objects.filter(ativo=True)


def _com_ultima_resposta(qs, usuario_id):
    ultima = AceiteTermo.objects.filter(usuario_id=usuario_id, termo=OuterRef("pk"))
    return qs.annotate(
        ultima_resposta=Subquery(ultima.values("aceito")[:1]),
        respondido_em=Subquery(ultima.values("criado_em")[:1]),
    )


def pendentes(usuario_id):
    """Termos ativos que o usuário ainda não respondeu na versão atual."""
    return _com_ultima_resposta(_ativos(), usuario_id).filter(
        ultima_resposta__isnull=True
    )


def tem_obrigatorio_pendente(usuario_id) -> bool:
    """Obrigatório sem resposta OU cuja última resposta não foi de aceite."""
    return (
        _com_ultima_resposta(_ativos().filter(obrigatorio=True), usuario_id)
        # exclude(ultima_resposta=True) deixaria de fora o NULL (sem resposta).
        .filter(Q(ultima_resposta__isnull=True) | Q(ultima_resposta=False))
        .exists()
    )


def do_usuario(usuario_id):
    """Termos ativos com a situação do usuário (para a tela Meus dados)."""
    return _com_ultima_resposta(_ativos(), usuario_id)


def serializar(termo, com_resposta=False) -> dict:
    item = {
        "id": termo.id,
        "titulo": termo.titulo,
        "texto": termo.texto,
        "versao": termo.versao,
        "obrigatorio": termo.obrigatorio,
        "ativo": termo.ativo,
        "criado_em": termo.criado_em.isoformat(),
    }
    if com_resposta:
        item["aceito"] = getattr(termo, "ultima_resposta", None)
        respondido = getattr(termo, "respondido_em", None)
        item["respondido_em"] = respondido.isoformat() if respondido else None
    return item


def responder(usuario_id, respostas) -> list:
    """Grava as respostas [{termo_id, aceito}] do usuário. Obrigatório só pode
    ser aceito. Devolve os termos ainda pendentes."""
    if not isinstance(respostas, list) or not respostas:
        raise DadosInvalidos("Envie as respostas aos termos.", "respostas")
    ativos = {t.id: t for t in _ativos()}
    validas = []
    for resposta in respostas:
        if not isinstance(resposta, dict):
            raise DadosInvalidos("Resposta inválida.", "respostas")
        termo = ativos.get(resposta.get("termo_id"))
        aceito = resposta.get("aceito")
        if termo is None:
            raise DadosInvalidos("Termo inexistente ou fora de vigor.", "termo_id")
        if not isinstance(aceito, bool):
            raise DadosInvalidos("Informe se o termo foi aceito.", "aceito")
        if termo.obrigatorio and not aceito:
            raise DadosInvalidos(
                f'O termo "{termo.titulo}" é obrigatório para usar o sistema.', "aceito"
            )
        validas.append((termo, aceito))

    with transaction.atomic():
        for termo, aceito in validas:
            AceiteTermo.objects.create(
                usuario_id=usuario_id, termo=termo, versao=termo.versao, aceito=aceito
            )
            registrar_acao(
                usuario_id,
                AcaoAuditoria.TERMO_ACEITO if aceito else AcaoAuditoria.TERMO_RECUSADO,
                EntidadeAuditoria.TERMO,
                termo.id,
            )
    return list(pendentes(usuario_id))


def cadastrar(admin_id, dados) -> Termo:
    """Cadastra um termo. Se já houver termo ativo com o mesmo título, ele vira
    a versão anterior (inativa) e o novo passa a valer: todos respondem de novo."""
    if not isinstance(dados, dict):
        raise DadosInvalidos("Dados de requisição inválidos.")
    titulo = (dados.get("titulo") or "").strip()
    texto = (dados.get("texto") or "").strip()
    versao = (dados.get("versao") or "").strip()
    obrigatorio = dados.get("obrigatorio", True)
    if not titulo or len(titulo) > TITULO_MAXIMO:
        raise DadosInvalidos(
            f"Informe o título (até {TITULO_MAXIMO} caracteres).", "titulo"
        )
    if not texto:
        raise DadosInvalidos("Informe o texto do termo.", "texto")
    if not versao or len(versao) > VERSAO_MAXIMA:
        raise DadosInvalidos(
            f"Informe a versão (até {VERSAO_MAXIMA} caracteres).", "versao"
        )
    if not isinstance(obrigatorio, bool):
        raise DadosInvalidos("Informe se o termo é obrigatório.", "obrigatorio")

    with transaction.atomic():
        anteriores = _ativos().filter(titulo__iexact=titulo)
        if anteriores.filter(versao=versao).exists():
            raise DadosInvalidos("Já existe este termo nesta versão.", "versao")
        anteriores.update(ativo=False)
        termo = Termo.objects.create(
            titulo=titulo,
            texto=texto,
            versao=versao,
            obrigatorio=obrigatorio,
            ativo=True,
            criado_por_id=admin_id,
        )
        registrar_acao(
            admin_id, AcaoAuditoria.CRIADO, EntidadeAuditoria.TERMO, termo.id
        )
    return termo


def todos():
    """Para o administrador: todos os termos, vigentes primeiro."""
    return Termo.objects.order_by("-ativo", "titulo", "-criado_em")
