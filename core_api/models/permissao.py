"""
Permissão de acesso a documentos por usuário.

Acesso = usuário + área + nível de sigilo máximo, e NUNCA herança automática
por setor/papel: quem não tem linha aqui não enxerga documento nenhum.

    usuario_permissao_area   regra geral: o usuário X pode acessar, na área
                             Y, documentos até o nível de sigilo Z.

Um documento é acessível ao usuário quando ele tem permissão em ALGUMA das
áreas do documento (principal ou secundária) e o nível de sigilo do
documento é menor ou igual ao máximo concedido NAQUELA área
(`NivelSigilo.ordinal`). A regra vive em
`DocumentoQuerySet.permitidos_para`, num lugar só.

`usuario` aponta para PerfilOperacional (a PK dele é o uuid do JWT, não um
bigserial como no diagrama físico). A tabela de exceção por documento
(`documento_restricao_especifica`, PERMITIR/NEGAR) fica para um próximo
incremento.
"""

from django.db import models

from core_api.models.perfil_operacional import PerfilOperacional
from core_api.models.taxonomia import Area, NivelSigilo


class UsuarioPermissaoArea(models.Model):
    usuario = models.ForeignKey(
        PerfilOperacional,
        on_delete=models.CASCADE,
        related_name="permissoes_area",
    )
    area = models.ForeignKey(
        Area, on_delete=models.CASCADE, related_name="permissoes_usuario"
    )
    nivel_sigilo_maximo = models.ForeignKey(
        NivelSigilo, on_delete=models.PROTECT, related_name="permissoes_area"
    )

    class Meta:
        app_label = "core_api"
        db_table = "usuario_permissao_area"
        constraints = [
            # Uma regra por usuário e área: conceder de novo muda o nível, não
            # cria outra linha (duas linhas com níveis diferentes seriam ambíguas).
            models.UniqueConstraint(
                fields=["usuario", "area"], name="uq_usuario_permissao_area"
            ),
        ]

    def __str__(self):
        return f"{self.usuario_id} - {self.area_id} até {self.nivel_sigilo_maximo_id}"
