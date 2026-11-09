"""Apoio dos testes de permissão de acesso a documento (S2-26)."""

from core_api.models import DocumentoArea, UsuarioPermissaoArea


def liberar(perfil, documento, nivel=None):
    """Dá ao `perfil` permissão em todas as áreas do `documento`, até o nível de
    sigilo do próprio documento (ou até `nivel`, se passado). Chamar de novo
    muda o nível em vez de duplicar a regra."""
    nivel = nivel or documento.nivel_sigilo
    for area_id in DocumentoArea.objects.filter(documento=documento).values_list(
        "area_id", flat=True
    ):
        UsuarioPermissaoArea.objects.update_or_create(
            usuario=perfil,
            area_id=area_id,
            defaults={"nivel_sigilo_maximo": nivel},
        )
