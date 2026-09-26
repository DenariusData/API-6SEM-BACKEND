"""
Testes de AKA-14 (tabelas/regras do banco de negócio) e AKA-11
(ausência de dados pessoais nesse mesmo banco).

TransactionTestCase, não TestCase: os triggers deferrable só disparam
no COMMIT, e TestCase padrão nunca comita de verdade.
"""

import uuid

from django.db import DatabaseError, IntegrityError, transaction
from django.test import TransactionTestCase

from core_api.models import (
    Area,
    Categoria,
    Documento,
    DocumentoArea,
    NivelSigilo,
    PerfilOperacional,
    Subcategoria,
    TipoDocumento,
)
from core_api.models.taxonomia import Idioma, StatusDocumentoChoices


def _criar_taxonomia():
    sufixo = uuid.uuid4().hex[:8]
    area = Area.objects.create(nome=f"Manutenção {sufixo}")
    categoria = Categoria.objects.create(area=area, nome="Normas Técnicas")
    subcategoria = Subcategoria.objects.create(
        categoria=categoria, nome="Compatibilidade de Materiais"
    )
    tipo = TipoDocumento.objects.create(nome=f"Norma ou regulamento {sufixo}")
    sigilo = NivelSigilo.objects.create(
        nome=f"INTERNO-{sufixo}", ordinal=int(sufixo[:4], 16) % 10000 + 100
    )
    idioma = Idioma.objects.create(codigo=sufixo[:8], nome="Português")
    perfil = PerfilOperacional.objects.create(
        usuario_id=uuid.uuid4(), matricula=f"AK-{sufixo}"
    )
    return area, subcategoria, tipo, sigilo, idioma, perfil


def _documento_valido(**overrides):
    """Cria um documento válido, já com área principal vinculada na MESMA
    transação (os triggers deferrable só checam no commit)."""
    area, subcategoria, tipo, sigilo, idioma, perfil = _criar_taxonomia()
    dados = dict(
        titulo="Compatibility of Materials Guideline",
        identificador_codigo="MAT-COMP-001",
        tipo_documento=tipo,
        numero_revisao="Rev. B",
        data_emissao="2025-03-10",
        subcategoria=subcategoria,
        status=StatusDocumentoChoices.VIGENTE,
        nivel_sigilo=sigilo,
        origem_fonte="Biblioteca pública",
        responsavel=perfil,
        idioma=idioma,
        arquivo_original_url="https://storage/doc.pdf",
        usuario_upload=perfil,
    )
    dados.update(overrides)
    with transaction.atomic():
        doc = Documento.objects.create(**dados)
        DocumentoArea.objects.create(documento=doc, area=area, is_principal=True)
    return doc, area


class DocumentoCamposObrigatoriosTestCase(TransactionTestCase):
    databases = {"default"}

    def test_documento_com_todos_os_campos_e_salvo(self):
        doc, _ = _documento_valido()
        self.assertIsNotNone(doc.id)

    def test_titulo_vazio_e_rejeitado_pelo_banco(self):
        area, subcategoria, tipo, sigilo, idioma, perfil = _criar_taxonomia()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Documento.objects.create(
                    titulo="",
                    identificador_codigo="X1",
                    tipo_documento=tipo,
                    numero_revisao="A",
                    data_emissao="2025-01-01",
                    subcategoria=subcategoria,
                    nivel_sigilo=sigilo,
                    origem_fonte="x",
                    responsavel=perfil,
                    idioma=idioma,
                    arquivo_original_url="x",
                    usuario_upload=perfil,
                )

    def test_tipo_documento_inexistente_e_rejeitado(self):
        area, subcategoria, _, sigilo, idioma, perfil = _criar_taxonomia()
        with self.assertRaises((IntegrityError, ValueError)):
            with transaction.atomic():
                Documento.objects.create(
                    titulo="Teste",
                    identificador_codigo="X2",
                    tipo_documento_id="00000000-0000-0000-0000-000000000000",
                    numero_revisao="A",
                    data_emissao="2025-01-01",
                    subcategoria=subcategoria,
                    nivel_sigilo=sigilo,
                    origem_fonte="x",
                    responsavel=perfil,
                    idioma=idioma,
                    arquivo_original_url="x",
                    usuario_upload=perfil,
                )

    def test_revisao_duplicada_do_mesmo_identificador_e_rejeitada(self):
        _documento_valido()
        area, subcategoria, tipo, sigilo, idioma, perfil = _criar_taxonomia()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Documento.objects.create(
                    titulo="Outro título",
                    identificador_codigo="MAT-COMP-001",
                    tipo_documento=tipo,
                    numero_revisao="Rev. B",
                    data_emissao="2025-01-01",
                    subcategoria=subcategoria,
                    nivel_sigilo=sigilo,
                    origem_fonte="x",
                    responsavel=perfil,
                    idioma=idioma,
                    arquivo_original_url="x",
                    usuario_upload=perfil,
                )


class DocumentoAreaTestCase(TransactionTestCase):
    databases = {"default"}

    def test_documento_pode_ter_area_principal_e_secundaria(self):
        doc, area_principal = _documento_valido()
        area_secundaria = Area.objects.create(nome="Segurança")
        DocumentoArea.objects.create(
            documento=doc, area=area_secundaria, is_principal=False
        )
        self.assertEqual(doc.areas.count(), 2)
        self.assertEqual(
            DocumentoArea.objects.get(documento=doc, is_principal=True).area,
            area_principal,
        )

    def test_documento_sem_nenhuma_area_e_rejeitado(self):
        area, subcategoria, tipo, sigilo, idioma, perfil = _criar_taxonomia()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Documento.objects.create(
                    titulo="Sem área",
                    identificador_codigo="SEM-AREA-001",
                    tipo_documento=tipo,
                    numero_revisao="Rev. A",
                    data_emissao="2025-01-01",
                    subcategoria=subcategoria,
                    nivel_sigilo=sigilo,
                    origem_fonte="x",
                    responsavel=perfil,
                    idioma=idioma,
                    arquivo_original_url="x",
                    usuario_upload=perfil,
                )

    def test_documento_com_duas_areas_principais_e_rejeitado(self):
        doc, area_a = _documento_valido()
        area_b = Area.objects.create(nome="Compliance")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DocumentoArea.objects.create(
                    documento=doc, area=area_b, is_principal=True
                )

    def test_area_governanca_central_e_unica(self):
        Area.objects.create(nome="Governança 1", eh_governanca_central=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Area.objects.create(nome="Governança 2", eh_governanca_central=True)


class TaxonomiaTresNiveisTestCase(TransactionTestCase):
    databases = {"default"}

    def test_hierarquia_area_categoria_subcategoria(self):
        area = Area.objects.create(nome="Operações")
        categoria = Categoria.objects.create(area=area, nome="Procedimentos")
        subcategoria = Subcategoria.objects.create(
            categoria=categoria, nome="Checklists"
        )
        self.assertEqual(subcategoria.categoria, categoria)
        self.assertEqual(subcategoria.categoria.area, area)
        self.assertIn(subcategoria, categoria.subcategorias.all())
        self.assertIn(categoria, area.categorias.all())


class DocumentoSemDadosPessoaisTestCase(TransactionTestCase):
    """AKA-11: garante que o banco de negócio ('default') não guarda
    identidade/credencial de usuário - isso vive só no banco 'credenciais_db'
    (app `credenciais`)."""

    databases = {"default"}

    COLUNAS_PROIBIDAS = {"email", "senha_hash", "telefone", "cpf", "rg"}
    PREFIXOS_INTERNOS_DJANGO = ("auth_", "django_", "admin_")

    def test_nenhuma_tabela_do_banco_de_negocio_tem_coluna_de_identidade(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                """
            )
            encontrados = [
                (tabela, coluna)
                for tabela, coluna in cursor.fetchall()
                if coluna in self.COLUNAS_PROIBIDAS
                and not tabela.startswith(self.PREFIXOS_INTERNOS_DJANGO)
            ]

        self.assertEqual(
            encontrados,
            [],
            f"Colunas de identidade encontradas no banco de negócio: {encontrados}",
        )

    def test_tabela_usuario_nao_existe_no_banco_de_negocio(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'usuario'
                """
            )
            self.assertEqual(
                cursor.fetchall(),
                [],
                "usuario não deveria existir - fica no banco de credenciais.",
            )
