"""
Carga inicial de taxonomia (AKA-14, critério de aceite: "Estrutura de
três níveis carregada com dados iniciais"). Idempotente (get_or_create).

Uso: python manage.py seed_taxonomia
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core_api.models import (
    Area,
    Categoria,
    Idioma,
    NivelSigilo,
    Subcategoria,
    TipoDocumento,
)

ESTRUTURA = {
    "Manutenção": {
        "Normas Técnicas": [
            "Compatibilidade de Materiais",
            "Procedimentos de Inspeção",
        ],
    },
    "Operações": {
        "Procedimentos Operacionais": ["Checklists de Voo", "Manuais de Operação"],
    },
    "Segurança": {
        "Segurança Operacional": ["Relatórios de Incidente", "Políticas de Segurança"],
    },
    "Compliance": {
        "Auditoria e Qualidade": ["Registros AS9100D", "Não Conformidades"],
    },
}

TIPOS_DOCUMENTO = [
    "Norma ou regulamento",
    "Procedimento interno",
    "Manual",
    "Instrução",
    "Circular",
    "Boletim",
    "Relatório",
    "Documento de fabricante",
    "Documento complementar ou de referência",
]

NIVEIS_SIGILO = [("PUBLICO", 1), ("INTERNO", 2), ("RESTRITO", 3), ("CONFIDENCIAL", 4)]

IDIOMAS = [("en", "Inglês"), ("pt", "Português")]


class Command(BaseCommand):
    help = "Popula a estrutura de 3 níveis (área/categoria/subcategoria) e os lookups do documento."

    @transaction.atomic
    def handle(self, *args, **options):
        criados = {
            "area": 0,
            "categoria": 0,
            "subcategoria": 0,
            "tipo": 0,
            "sigilo": 0,
            "idioma": 0,
        }

        for nome_area, categorias in ESTRUTURA.items():
            area, foi_criada = Area.objects.get_or_create(nome=nome_area)
            criados["area"] += int(foi_criada)
            for nome_categoria, subcategorias in categorias.items():
                categoria, foi_criada = Categoria.objects.get_or_create(
                    area=area, nome=nome_categoria
                )
                criados["categoria"] += int(foi_criada)
                for nome_sub in subcategorias:
                    _, foi_criada = Subcategoria.objects.get_or_create(
                        categoria=categoria, nome=nome_sub
                    )
                    criados["subcategoria"] += int(foi_criada)

        for nome_tipo in TIPOS_DOCUMENTO:
            _, foi_criada = TipoDocumento.objects.get_or_create(nome=nome_tipo)
            criados["tipo"] += int(foi_criada)

        for nome_sigilo, ordinal in NIVEIS_SIGILO:
            _, foi_criada = NivelSigilo.objects.get_or_create(
                nome=nome_sigilo, defaults={"ordinal": ordinal}
            )
            criados["sigilo"] += int(foi_criada)

        for codigo, nome_idioma in IDIOMAS:
            _, foi_criada = Idioma.objects.get_or_create(
                codigo=codigo, defaults={"nome": nome_idioma}
            )
            criados["idioma"] += int(foi_criada)

        self.stdout.write(self.style.SUCCESS(f"Seed de taxonomia concluído: {criados}"))
