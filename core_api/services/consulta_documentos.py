"""
Consultas do fluxo de validação (S2-35): fila de pendentes, "meus
documentos" e detalhe com histórico.

Regras (todas aqui, no backend):
  - fila: só AGUARDANDO_VALIDACAO, SEM os documentos enviados por quem está
    consultando, do mais antigo para o mais novo;
  - meus: só os envios de quem consulta, com o estado e o último comentário
    de devolução; filtro opcional por status_validacao;
  - detalhe: documento DISPONIVEL é visto por qualquer usuário autenticado;
    documento em outro estado só pelo autor e pelos validadores
    (ADMINISTRADOR). Para os demais é como se não existisse (404, nunca 403,
    para não confirmar que o id existe);
  - listas paginadas, 20 por página;
  - pessoas aparecem só pelo NOME, nunca com e-mail.

O nome da pessoa vive em `credenciais_db` (`Usuario.nome`) e o documento só
guarda o uuid do perfil operacional, então os nomes são buscados em UMA
consulta por requisição (nada de consulta por linha), por valor de uuid,
como o resto do projeto faz entre os dois bancos.

`usuario_id` é o uuid do JWT (`sub`), que é a própria chave do
PerfilOperacional: não precisa carregar o perfil para consultar.
"""

from django.core.paginator import EmptyPage, Paginator
from django.db.models import OuterRef, Subquery, TextField
from django.db.models.functions import Coalesce

from core_api.models import (
    AcaoValidacao,
    Documento,
    HistoricoValidacao,
    StatusValidacao,
)
from credenciais.models import Papel, Usuario

TAMANHO_PAGINA = 20


class ParametroInvalido(Exception):
    """Parâmetro de consulta inválido (HTTP 400). `campo` diz qual."""

    def __init__(self, mensagem, campo):
        super().__init__(mensagem)
        self.campo = campo


# -- helpers ------------------------------------------------------------
def nomes_dos_usuarios(ids) -> dict:
    """{uuid: nome} dos usuários pedidos, numa única consulta a credenciais_db.
    Usuário que não existe mais fica de fora do dicionário."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    usuarios = Usuario.objects.using("credenciais_db").filter(id__in=ids)
    return {u.id: u.nome for u in usuarios.only("id", "nome")}


def paginar(queryset, pagina_bruta, serializar):
    """Pagina o queryset (já ordenado). `serializar(itens)` recebe a lista da
    página e devolve a lista de dicts (assim os nomes são buscados 1 vez por
    página). Página além da última devolve lista vazia, com os totais certos."""
    try:
        pagina = int(pagina_bruta) if pagina_bruta not in (None, "") else 1
    except (TypeError, ValueError):
        raise ParametroInvalido("A página deve ser um número inteiro.", "page")
    if pagina < 1:
        raise ParametroInvalido("A página deve ser maior ou igual a 1.", "page")

    paginador = Paginator(queryset, TAMANHO_PAGINA)
    try:
        itens = list(paginador.page(pagina).object_list)
    except EmptyPage:
        itens = []
    return {
        "count": paginador.count,
        "page": pagina,
        "page_size": TAMANHO_PAGINA,
        "total_pages": paginador.num_pages,
        "results": serializar(itens),
    }


def _iso(valor):
    return valor.isoformat() if valor else None


def _base():
    return Documento.objects.select_related(
        "tipo_documento", "nivel_sigilo", "nivel_sigilo_sugerido"
    )


# -- listas -------------------------------------------------------------
def fila_de_pendentes(usuario_id):
    """AGUARDANDO_VALIDACAO sem os enviados por `usuario_id`, mais antigo primeiro."""
    return (
        _base()
        .filter(status_validacao=StatusValidacao.AGUARDANDO_VALIDACAO)
        .exclude(usuario_upload_id=usuario_id)
        .order_by("criado_em", "id")
    )


def meus_documentos(usuario_id, status=None):
    """Envios de `usuario_id`, mais recente primeiro, com o último comentário
    de devolução. `status` filtra por status_validacao (valor inválido levanta
    ParametroInvalido)."""
    ultima_devolucao = (
        HistoricoValidacao.objects.filter(
            documento=OuterRef("pk"), acao=AcaoValidacao.DEVOLVIDO
        )
        .order_by("-criado_em", "-id")
        .values("comentario")[:1]
    )
    qs = (
        _base()
        .filter(usuario_upload_id=usuario_id)
        .annotate(
            # Vem do histórico (sobrevive a um reenvio); se o documento foi
            # devolvido sem linha de histórico, cai no campo do próprio documento.
            ultimo_comentario_devolucao=Coalesce(
                Subquery(ultima_devolucao, output_field=TextField()),
                "comentario_devolucao",
            )
        )
        .order_by("-criado_em", "-id")
    )
    if status not in (None, ""):
        valor = str(status).strip().upper()
        if valor not in StatusValidacao.values:
            raise ParametroInvalido(
                "Status inválido. Use: " + ", ".join(StatusValidacao.values) + ".",
                "status",
            )
        qs = qs.filter(status_validacao=valor)
    return qs


def serializar_lista(documentos, com_ultimo_comentario=False):
    nomes = nomes_dos_usuarios(d.usuario_upload_id for d in documentos)
    itens = []
    for d in documentos:
        item = {
            "id": str(d.id),
            "titulo": d.titulo,
            "identificador_codigo": d.identificador_codigo,
            "tipo_documento": d.tipo_documento.nome,
            "numero_revisao": d.numero_revisao,
            "nivel_sigilo": d.nivel_sigilo.nome,
            "nivel_sigilo_sugerido": d.nivel_sigilo_sugerido.nome,
            "status_validacao": d.status_validacao,
            "status_processamento": d.status_processamento,
            "autor": nomes.get(d.usuario_upload_id),
            "criado_em": _iso(d.criado_em),
        }
        if com_ultimo_comentario:
            item["ultimo_comentario_devolucao"] = d.ultimo_comentario_devolucao
        itens.append(item)
    return itens


# -- detalhe ------------------------------------------------------------
def pode_ver(documento, usuario_id, papel_codigo) -> bool:
    if documento.status_validacao == StatusValidacao.DISPONIVEL:
        return True
    if papel_codigo == Papel.ADMINISTRADOR:
        return True
    return usuario_id is not None and documento.usuario_upload_id == usuario_id


def detalhe(documento_id, usuario_id, papel_codigo) -> dict:
    """Metadados + histórico (por data). Levanta Documento.DoesNotExist se o
    documento não existe OU se o usuário não pode vê-lo (mesmo 404)."""
    doc = Documento.objects.select_related(
        "tipo_documento",
        "nivel_sigilo",
        "nivel_sigilo_sugerido",
        "idioma",
        "subcategoria",
    ).get(pk=documento_id)
    if not pode_ver(doc, usuario_id, papel_codigo):
        raise Documento.DoesNotExist

    historico = list(
        HistoricoValidacao.objects.filter(documento=doc)
        .select_related("nivel_sigilo")
        .order_by("criado_em", "id")
    )
    nomes = nomes_dos_usuarios(
        [doc.usuario_upload_id, doc.validado_por_id, doc.responsavel_id]
        + [h.usuario_id for h in historico]
    )
    return {
        "id": str(doc.id),
        "titulo": doc.titulo,
        "identificador_codigo": doc.identificador_codigo,
        "tipo_documento": doc.tipo_documento.nome,
        "numero_revisao": doc.numero_revisao,
        "data_emissao": _iso(doc.data_emissao),
        "data_vigencia": _iso(doc.data_vigencia),
        "data_validade": _iso(doc.data_validade),
        "status": doc.status,
        "subcategoria": doc.subcategoria.nome,
        "areas": sorted(doc.areas.values_list("nome", flat=True)),
        "idioma": doc.idioma.nome,
        "origem_fonte": doc.origem_fonte,
        "arquivo_original_url": doc.arquivo_original_url,
        "nivel_sigilo": doc.nivel_sigilo.nome,
        "nivel_sigilo_sugerido": doc.nivel_sigilo_sugerido.nome,
        "status_validacao": doc.status_validacao,
        "status_processamento": doc.status_processamento,
        "erro_processamento": doc.erro_processamento,
        "comentario_devolucao": doc.comentario_devolucao,
        "autor": nomes.get(doc.usuario_upload_id),
        "responsavel": nomes.get(doc.responsavel_id),
        "validado_por": nomes.get(doc.validado_por_id),
        "validado_em": _iso(doc.validado_em),
        "criado_em": _iso(doc.criado_em),
        "atualizado_em": _iso(doc.atualizado_em),
        "historico": [
            {
                "acao": h.acao,
                "usuario": nomes.get(h.usuario_id),
                "comentario": h.comentario,
                "nivel_sigilo": h.nivel_sigilo.nome if h.nivel_sigilo else None,
                "criado_em": _iso(h.criado_em),
            }
            for h in historico
        ],
    }
