"""
Taxonomia de 3 níveis: Area > Categoria > Subcategoria (AKA-14).
Lookups do documento extensíveis sem migração (R1P3, R2P2, R2P5).
Tudo neste arquivo vive no banco "default" (negócio).
"""

from django.db import models


class Area(models.Model):
    nome = models.CharField(max_length=120, unique=True)
    descricao = models.CharField(max_length=500, blank=True, default="")
    eh_governanca_central = models.BooleanField(
        default=False,
        help_text="Área que recebe solicitações sem responsável identificável.",
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "area"
        constraints = [
            models.UniqueConstraint(
                fields=["eh_governanca_central"],
                condition=models.Q(eh_governanca_central=True),
                name="uq_area_governanca_central",
            ),
        ]

    def __str__(self):
        return self.nome


class Categoria(models.Model):
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="categorias")
    nome = models.CharField(max_length=120)
    descricao = models.CharField(max_length=500, blank=True, default="")
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "categoria"
        constraints = [
            models.UniqueConstraint(
                fields=["area", "nome"], name="uq_categoria_area_nome"
            ),
        ]

    def __str__(self):
        return f"{self.area.nome} / {self.nome}"


class Subcategoria(models.Model):
    categoria = models.ForeignKey(
        Categoria, on_delete=models.PROTECT, related_name="subcategorias"
    )
    nome = models.CharField(max_length=120)
    descricao = models.CharField(max_length=500, blank=True, default="")
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "subcategoria"
        constraints = [
            models.UniqueConstraint(
                fields=["categoria", "nome"], name="uq_subcategoria_categoria_nome"
            ),
        ]

    def __str__(self):
        return f"{self.categoria} / {self.nome}"


class TipoDocumento(models.Model):
    nome = models.CharField(max_length=60, unique=True)

    class Meta:
        app_label = "core_api"
        db_table = "tipo_documento"

    def __str__(self):
        return self.nome


class NivelSigilo(models.Model):
    nome = models.CharField(max_length=20, unique=True)
    ordinal = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        app_label = "core_api"
        db_table = "nivel_sigilo"

    def __str__(self):
        return self.nome


class Idioma(models.Model):
    codigo = models.CharField(max_length=10, primary_key=True)
    nome = models.CharField(max_length=40)

    class Meta:
        app_label = "core_api"
        db_table = "idioma"

    def __str__(self):
        return self.codigo


class StatusDocumentoChoices(models.TextChoices):
    VIGENTE = "VIGENTE", "Vigente"
    SUBSTITUIDO = "SUBSTITUIDO", "Substituído"
    CANCELADO = "CANCELADO", "Cancelado"
    EM_REVISAO = "EM_REVISAO", "Em revisão"
