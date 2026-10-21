"""
Popula as tabelas de opções do upload (S2-32): tipos de documento, níveis de
sigilo, idiomas e áreas -> categorias -> subcategorias.

Uso:
    python manage.py popular_opcoes_upload

Pode rodar quantas vezes quiser: usa get_or_create, então não duplica nada.

O comando lê os campos dos models em tempo de execução e só preenche os que
existem (nome/name, codigo, ativo, ordem...). Se algum model exigir um campo
obrigatório que não está aqui, o erro diz qual é.

ATENÇÃO: TIPOS e AREAS abaixo são EXEMPLOS para desenvolvimento.
Troque pelos 9 tipos e pelas áreas reais do cliente antes de usar em
homologação/produção.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core_api.models import Area, Categoria, Idioma, NivelSigilo, Subcategoria, TipoDocumento

# --- Dados -------------------------------------------------------------------

NIVEIS_SIGILO = [  # definidos pelo cliente
    # "ordinal" é o nome da coluna no model; "ordem" fica de reserva
    {"nome": "Público", "codigo": "PUBLICO", "ordinal": 1, "ordem": 1},
    {"nome": "Interno", "codigo": "INTERNO", "ordinal": 2, "ordem": 2},
    {"nome": "Restrito", "codigo": "RESTRITO", "ordinal": 3, "ordem": 3},
    {"nome": "Confidencial", "codigo": "CONFIDENCIAL", "ordinal": 4, "ordem": 4},
]

IDIOMAS = [
    {"nome": "Português (Brasil)", "codigo": "pt-BR"},
    {"nome": "Inglês", "codigo": "en"},
    {"nome": "Espanhol", "codigo": "es"},
]

# EXEMPLO - trocar pelos 9 tipos do cliente
TIPOS_DOCUMENTO = [
    "Manual",
    "Procedimento",
    "Instrução de Trabalho",
    "Especificação Técnica",
    "Desenho Técnico",
    "Relatório Técnico",
    "Norma Interna",
    "Plano de Inspeção",
    "Formulário",
]

# EXEMPLO - trocar pelas áreas/categorias/subcategorias do cliente
AREAS = {
    "Engenharia": {
        "Aeroestrutura": ["Fuselagem", "Asa", "Empenagem"],
        "Sistemas": ["Elétrico", "Hidráulico"],
    },
    "Qualidade": {
        "Inspeção": ["Recebimento", "Final"],
        "Auditoria": ["Interna", "Fornecedor"],
    },
    "Manutenção": {
        "Preventiva": ["Programada", "Rotina"],
        "Corretiva": ["Emergencial"],
    },
}

# --- Utilitários -------------------------------------------------------------

CAMPOS_NOME = ("nome", "name", "descricao", "titulo", "label")


def _campos(model):
    return {f.name: f for f in model._meta.get_fields() if getattr(f, "concrete", False)}


def _campo_nome(model):
    campos = _campos(model)
    for nome in CAMPOS_NOME:
        if nome in campos:
            return nome
    raise CommandError(f"{model.__name__}: não achei um campo de nome ({', '.join(CAMPOS_NOME)}).")


def _fk_para(model, alvo):
    """Nome do campo de `model` que aponta para `alvo` (ex.: Categoria.area)."""
    for f in _campos(model).values():
        if f.is_relation and f.related_model is alvo:
            return f.name
    raise CommandError(f"{model.__name__} não tem FK para {alvo.__name__}.")


def criar(model, dados, **relacoes):
    """get_or_create usando só os campos que existem no model."""
    campos = _campos(model)
    dados = dict(dados)
    # "nome" vira o campo de nome real do model (name, descricao...)
    if "nome" in dados and "nome" not in campos:
        dados[_campo_nome(model)] = dados.pop("nome")
    dados = {k: v for k, v in dados.items() if k in campos}
    if "ativo" in campos:
        dados.setdefault("ativo", True)

    # chave de busca: código se existir (ex.: pk do Idioma), senão o nome
    if "codigo" in dados:
        chave = {"codigo": dados.pop("codigo")}
    else:
        nome_campo = _campo_nome(model)
        chave = {nome_campo: dados.pop(nome_campo)}

    try:
        obj, criado = model.objects.get_or_create(**chave, **relacoes, defaults=dados)
    except IntegrityError as erro:
        obrigatorios = [
            f.name for f in campos.values()
            if not f.primary_key and not f.null and not f.has_default()
        ]
        raise CommandError(
            f"{model.__name__} {chave}: falta algum campo obrigatório. "
            f"Campos sem default no model: {obrigatorios}. Detalhe: {erro}"
        )
    return obj, criado


# --- Comando -----------------------------------------------------------------

class Command(BaseCommand):
    help = "Popula tipos, níveis de sigilo, idiomas e áreas/categorias/subcategorias do upload."

    @transaction.atomic
    def handle(self, *args, **options):
        total = 0

        for n in NIVEIS_SIGILO:
            total += criar(NivelSigilo, n)[1]

        for i in IDIOMAS:
            total += criar(Idioma, i)[1]

        for nome in TIPOS_DOCUMENTO:
            total += criar(TipoDocumento, {"nome": nome})[1]

        fk_area = _fk_para(Categoria, Area)
        fk_categoria = _fk_para(Subcategoria, Categoria)
        for nome_area, categorias in AREAS.items():
            area, criado = criar(Area, {"nome": nome_area})
            total += criado
            for nome_cat, subcategorias in categorias.items():
                cat, criado = criar(Categoria, {"nome": nome_cat}, **{fk_area: area})
                total += criado
                for nome_sub in subcategorias:
                    total += criar(Subcategoria, {"nome": nome_sub}, **{fk_categoria: cat})[1]

        self.stdout.write(self.style.SUCCESS(f"Pronto: {total} registro(s) novo(s)."))
        self.stdout.write(
            f"  TipoDocumento={TipoDocumento.objects.count()}  NivelSigilo={NivelSigilo.objects.count()}  "
            f"Idioma={Idioma.objects.count()}  Area={Area.objects.count()}  "
            f"Categoria={Categoria.objects.count()}  Subcategoria={Subcategoria.objects.count()}"
        )