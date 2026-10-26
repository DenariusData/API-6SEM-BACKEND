"""
Opções dos selects do formulário de upload (S2-32).

    tipos_documento()   os 9 tipos do cliente
    areas()             áreas ativas com categorias e subcategorias ativas
    niveis_sigilo()     do menos para o mais restrito (ordinal)
    idiomas()
    responsaveis(area)  usuários ativos que podem ser responsáveis

Responsáveis saem só com id e nome (LGPD): nada de e-mail, papel ou
matrícula. O nome vive em `credenciais_db`; o id é o uuid do
PerfilOperacional, que é o valor que o upload espera em `responsavel`.

Filtro `?area=`: ainda não existe ligação entre usuário e área no banco
(`usuario_permissao_area`, modelagem 1.5, não foi implementada). Por isso a
área é validada, mas a lista traz todos os usuários ativos. Quando a ligação
existir, o filtro entra aqui sem mudar o contrato da rota.
"""

from django.db.models import Prefetch

from core_api.models import (
    Area,
    Categoria,
    Idioma,
    NivelSigilo,
    PerfilOperacional,
    Subcategoria,
    TipoDocumento,
)
from core_api.services.consulta_documentos import ParametroInvalido
from credenciais.models import Usuario


def tipos_documento():
    return [
        {"id": t.id, "nome": t.nome} for t in TipoDocumento.objects.order_by("nome")
    ]


def areas():
    subcategorias = Subcategoria.objects.filter(ativo=True).order_by("nome")
    categorias = (
        Categoria.objects.filter(ativo=True)
        .order_by("nome")
        .prefetch_related(Prefetch("subcategorias", queryset=subcategorias))
    )
    queryset = (
        Area.objects.filter(ativo=True)
        .order_by("nome")
        .prefetch_related(Prefetch("categorias", queryset=categorias))
    )
    return [
        {
            "id": area.id,
            "nome": area.nome,
            "categorias": [
                {
                    "id": categoria.id,
                    "nome": categoria.nome,
                    "subcategorias": [
                        {"id": sub.id, "nome": sub.nome}
                        for sub in categoria.subcategorias.all()
                    ],
                }
                for categoria in area.categorias.all()
            ],
        }
        for area in queryset
    ]


def niveis_sigilo():
    return [
        {"id": n.id, "nome": n.nome} for n in NivelSigilo.objects.order_by("ordinal")
    ]


def idiomas():
    return [
        {"codigo": i.codigo, "nome": i.nome} for i in Idioma.objects.order_by("nome")
    ]


def responsaveis(area=None):
    """Usuários ativos com perfil operacional, por nome. `area`, se vier,
    precisa ser uma área ativa (ver docstring do módulo)."""
    if area not in (None, ""):
        try:
            existe = Area.objects.filter(pk=int(area), ativo=True).exists()
        except (TypeError, ValueError):
            existe = False
        if not existe:
            raise ParametroInvalido("Área inexistente.", "area")

    com_perfil = PerfilOperacional.objects.values_list("usuario_id", flat=True)
    usuarios = (
        Usuario.objects.using("credenciais_db")
        .filter(ativo=True, id__in=list(com_perfil))
        .order_by("nome")
        .only("id", "nome")
    )
    return [{"id": str(u.id), "nome": u.nome} for u in usuarios]
