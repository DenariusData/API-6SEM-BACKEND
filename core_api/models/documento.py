"""
Documento — entidade principal (AKA-14 / R2P3).

`responsavel`/`usuario_upload`/`aprovador` referenciam PerfilOperacional
(core_api.models.perfil_operacional), que já existe e vive no MESMO
banco "default" — por isso são ForeignKey de verdade, com integridade
referencial garantida pelo banco. NÃO referenciam credenciais.Usuario
diretamente (banco diferente, sem FK física possível/permitida — ver
credenciais/db_router.py).

`conteudo` é nullable de propósito: é o texto extraído via OCR (R1P2),
preenchido pelo pipeline depois do upload, não no momento do INSERT.

ESCOPO DESTA ENTREGA: status_workflow/aprovador (R1P7) e demais campos
de governança avançada (autorização de IA, controle de acesso granular,
relações, solicitação) ficam para um próximo incremento.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core_api.models.perfil_operacional import PerfilOperacional
from core_api.models.taxonomia import (
    Area,
    Idioma,
    NivelSigilo,
    StatusDocumentoChoices,
    Subcategoria,
    TipoDocumento,
)


class Documento(models.Model):
    # UUID explícito - Django usa BigAutoField por padrão, o que
    # quebraria os triggers em 0002_taxonomia_e_documento.py, que
    # esperam UUID.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Campos obrigatórios no upload (R2P3)
    titulo = models.CharField(max_length=300)
    identificador_codigo = models.CharField(max_length=100)
    tipo_documento = models.ForeignKey(
        TipoDocumento, on_delete=models.PROTECT, related_name="documentos"
    )
    numero_revisao = models.CharField(max_length=30)
    data_emissao = models.DateField()
    subcategoria = models.ForeignKey(
        Subcategoria, on_delete=models.PROTECT, related_name="documentos"
    )
    status = models.CharField(
        max_length=20,
        choices=StatusDocumentoChoices.choices,
        default=StatusDocumentoChoices.VIGENTE,
    )
    nivel_sigilo = models.ForeignKey(
        NivelSigilo, on_delete=models.PROTECT, related_name="documentos"
    )
    origem_fonte = models.CharField(max_length=200)
    responsavel = models.ForeignKey(
        PerfilOperacional,
        on_delete=models.PROTECT,
        related_name="documentos_responsavel",
    )
    idioma = models.ForeignKey(
        Idioma, on_delete=models.PROTECT, related_name="documentos"
    )
    arquivo_original_url = models.CharField(max_length=500)

    # Quando aplicável
    data_vigencia = models.DateField(null=True, blank=True)
    data_validade = models.DateField(null=True, blank=True)

    # Campos de sistema
    usuario_upload = models.ForeignKey(
        PerfilOperacional, on_delete=models.PROTECT, related_name="documentos_upload"
    )
    conteudo = models.TextField(
        null=True,
        blank=True,
        help_text="Texto extraído via OCR - preenchido pelo pipeline, não no upload.",
    )

    areas = models.ManyToManyField(
        Area, through="DocumentoArea", related_name="documentos"
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "core_api"
        db_table = "documento"
        constraints = [
            models.UniqueConstraint(
                fields=["identificador_codigo", "numero_revisao"],
                name="uq_documento_identificador_revisao",
            ),
            models.CheckConstraint(
                condition=~models.Q(titulo=""), name="ck_documento_titulo_nao_vazio"
            ),
            models.CheckConstraint(
                condition=~models.Q(identificador_codigo=""),
                name="ck_documento_identificador_nao_vazio",
            ),
            models.CheckConstraint(
                condition=~models.Q(numero_revisao=""),
                name="ck_documento_numero_revisao_nao_vazio",
            ),
            models.CheckConstraint(
                condition=~models.Q(origem_fonte=""),
                name="ck_documento_origem_nao_vazia",
            ),
            models.CheckConstraint(
                condition=~models.Q(arquivo_original_url=""),
                name="ck_documento_arquivo_nao_vazio",
            ),
        ]

    def __str__(self):
        return f"{self.identificador_codigo} ({self.numero_revisao})"


class DocumentoArea(models.Model):
    documento = models.ForeignKey(Documento, on_delete=models.CASCADE)
    area = models.ForeignKey(Area, on_delete=models.CASCADE)
    is_principal = models.BooleanField(default=False)

    class Meta:
        app_label = "core_api"
        db_table = "documento_area"
        constraints = [
            models.UniqueConstraint(
                fields=["documento", "area"], name="uq_documento_area"
            ),
            models.UniqueConstraint(
                fields=["documento"],
                condition=models.Q(is_principal=True),
                name="uq_documento_area_principal",
            ),
        ]

    def clean(self):
        if self.is_principal is False:
            existe_principal = (
                DocumentoArea.objects.filter(
                    documento_id=self.documento_id, is_principal=True
                )
                .exclude(pk=self.pk)
                .exists()
            )
            if not existe_principal:
                raise ValidationError(
                    "Documento precisa ter exatamente uma área principal."
                )

    def __str__(self):
        marca = "principal" if self.is_principal else "secundária"
        return f"{self.documento_id} - {self.area_id} ({marca})"
