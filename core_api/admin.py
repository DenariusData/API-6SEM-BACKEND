from django.contrib import admin

from core_api.models import (
    Area,
    Categoria,
    Chunk,
    Documento,
    DocumentoArea,
    EmbeddingVersion,
    Idioma,
    LogAcesso,
    LogAuditoria,
    NivelSigilo,
    PerfilOperacional,
    Subcategoria,
    TipoDocumento,
)


class DocumentoAreaInline(admin.TabularInline):
    model = DocumentoArea
    extra = 1


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = (
        "identificador_codigo",
        "numero_revisao",
        "titulo",
        "status",
        "nivel_sigilo",
    )
    list_filter = ("status", "nivel_sigilo", "tipo_documento")
    search_fields = ("titulo", "identificador_codigo")
    inlines = [DocumentoAreaInline]


admin.site.register(Area)
admin.site.register(Categoria)
admin.site.register(Subcategoria)
admin.site.register(TipoDocumento)
admin.site.register(NivelSigilo)
admin.site.register(Idioma)
admin.site.register(Chunk)
admin.site.register(EmbeddingVersion)
admin.site.register(PerfilOperacional)


@admin.register(LogAcesso, LogAuditoria)
class LogSomenteLeituraAdmin(admin.ModelAdmin):
    """Log só de inclusão (AKA-80): no admin dá para consultar, nunca
    incluir, editar ou apagar."""

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
