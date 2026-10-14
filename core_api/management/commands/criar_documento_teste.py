"""
Cria um Documento de teste AGUARDANDO_VALIDACAO, para exercitar os endpoints
de aprovar/devolver (S2-34) no Postman sem depender do upload, que ainda não
grava Documento no banco.

Uso:
    python manage.py criar_documento_teste
    python manage.py criar_documento_teste --autor qualidade@akaer.com.br
    python manage.py criar_documento_teste --nivel CONFIDENCIAL

O autor (`usuario_upload`) NÃO pode ser quem vai aprovar: por padrão é o
usuário de engenharia, e você aprova com o admin. Pré-requisitos:
migrate (dois bancos), seed_taxonomia e seed_usuarios_demo.

Como o seed antigo diz "já existe" sem criar o PerfilOperacional que falta,
este comando cria o perfil de todo usuário de credenciais_db que ainda não
tem um (sem isso o admin recebe 403 "perfil operacional não encontrado").
Só para desenvolvimento.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core_api.models import (
    Documento,
    DocumentoArea,
    Idioma,
    NivelSigilo,
    PerfilOperacional,
    Subcategoria,
    TipoDocumento,
)
from credenciais.models import Usuario


class Command(BaseCommand):
    help = "Cria um documento de teste aguardando validação e imprime o id."

    def add_arguments(self, parser):
        parser.add_argument("--autor", default="engenharia@akaer.com.br")
        parser.add_argument("--nivel", default="INTERNO")

    def _garantir_perfis(self):
        for u in Usuario.objects.using("credenciais_db").all():
            if PerfilOperacional.objects.filter(pk=u.id).exists():
                continue
            PerfilOperacional.objects.create(
                usuario_id=u.id, matricula=f"AK-T{uuid.uuid4().hex[:6].upper()}"
            )
            self.stdout.write(f"perfil operacional criado para {u.email}")

    def handle(self, *args, **opts):
        usuario = (
            Usuario.objects.using("credenciais_db").filter(email=opts["autor"]).first()
        )
        if usuario is None:
            raise CommandError(
                f"Usuário {opts['autor']} não existe. Rode seed_usuarios_demo."
            )
        self._garantir_perfis()
        autor = PerfilOperacional.objects.get(pk=usuario.id)

        nivel = NivelSigilo.objects.filter(nome=opts["nivel"].upper()).first()
        sub = Subcategoria.objects.select_related("categoria__area").first()
        tipo = TipoDocumento.objects.first()
        idioma = Idioma.objects.first()
        if not all([nivel, sub, tipo, idioma]):
            raise CommandError(
                "Taxonomia incompleta (nível, subcategoria, tipo ou idioma). "
                "Rode seed_taxonomia."
            )

        sufixo = uuid.uuid4().hex[:6].upper()
        with transaction.atomic():
            doc = Documento.objects.create(
                titulo=f"Documento de teste {sufixo}",
                identificador_codigo=f"TESTE-{sufixo}",
                tipo_documento=tipo,
                numero_revisao="A",
                data_emissao="2025-01-01",
                subcategoria=sub,
                status="VIGENTE",
                nivel_sigilo=nivel,
                nivel_sigilo_sugerido=nivel,
                origem_fonte="teste manual",
                responsavel=autor,
                idioma=idioma,
                arquivo_original_url="https://exemplo.local/teste.pdf",
                usuario_upload=autor,
            )
            DocumentoArea.objects.create(
                documento=doc, area=sub.categoria.area, is_principal=True
            )

        self.stdout.write(f"Documento criado: {doc.id}")
        self.stdout.write(f"  autor (upload): {opts['autor']}")
        self.stdout.write(f"  nivel: {nivel.nome} | status: {doc.status_validacao}")
        self.stdout.write(f"  POST /api/documents/{doc.id}/aprovar/")
