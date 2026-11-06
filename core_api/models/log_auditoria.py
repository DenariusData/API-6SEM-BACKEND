"""
Log de auditoria (AKA-31, épico E5 — Privacidade e LGPD).

Uma linha por ação: quem fez (`usuario_id`), o que fez (`acao`), em qual
registro (`entidade` + `registro_id`) e quando (`criado_em`). A gravação é
automática (signals em core_api/signals.py); ações sem model próprio, como
o login, usam `core_api.services.auditoria.registrar_acao`.

O que NUNCA entra aqui (LGPD):
  - nome, e-mail ou telefone de ninguém: o usuário aparece só pelo uuid do
    PerfilOperacional, sem FK (o log sobrevive ao perfil e não puxa dado
    pessoal por JOIN);
  - valores de campo: `campos_alterados` guarda só os NOMES dos campos que
    mudaram, nunca o valor antigo ou o novo;
  - texto livre: nas ações do fluxo de validação (ENVIADO, DEVOLVIDO,
    REENVIADO, APROVADO) o log guarda só o id da linha em
    HistoricoValidacao. O comentário do validador fica no histórico.

`acao` e `entidade` são listas fixas (TextChoices), nunca texto livre.
`LogAcesso` continua sendo o registro de visualização de documento; este
log cobre as demais ações.
"""

from django.db import models


class AcaoAuditoria(models.TextChoices):
    CRIADO = "CRIADO", "Criado"
    ATUALIZADO = "ATUALIZADO", "Atualizado"
    EXCLUIDO = "EXCLUIDO", "Excluído"
    # Fluxo de validação: mesmos valores de AcaoValidacao.
    ENVIADO = "ENVIADO", "Enviado"
    DEVOLVIDO = "DEVOLVIDO", "Devolvido"
    REENVIADO = "REENVIADO", "Reenviado"
    APROVADO = "APROVADO", "Aprovado"
    # Ações sem model próprio (registrar_acao).
    LOGIN = "LOGIN", "Login"
    LOGIN_FALHOU = "LOGIN_FALHOU", "Login falhou"
    DADOS_PESSOAIS_ALTERADOS = "DADOS_PESSOAIS_ALTERADOS", "Dados pessoais alterados"


class EntidadeAuditoria(models.TextChoices):
    DOCUMENTO = "DOCUMENTO", "Documento"
    DOCUMENTO_AREA = "DOCUMENTO_AREA", "Área do documento"
    HISTORICO_VALIDACAO = "HISTORICO_VALIDACAO", "Histórico de validação"
    USUARIO = "USUARIO", "Usuário"


class LogAuditoria(models.Model):
    # Nulo quando não há usuário identificado: login com e-mail que não
    # existe, ou ação feita por comando de manutenção, fora de requisição.
    usuario_id = models.UUIDField(null=True, blank=True)
    acao = models.CharField(max_length=30, choices=AcaoAuditoria.choices)
    entidade = models.CharField(max_length=30, choices=EntidadeAuditoria.choices)
    # Texto porque as chaves variam: uuid em Documento, inteiro no resto.
    registro_id = models.CharField(max_length=64, null=True, blank=True)
    campos_alterados = models.JSONField(default=list, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "log_auditoria"
        ordering = ["criado_em", "id"]
        indexes = [
            models.Index(
                fields=["entidade", "registro_id"], name="log_auditoria_registro_idx"
            ),
            models.Index(
                fields=["usuario_id", "criado_em"], name="log_auditoria_usuario_idx"
            ),
        ]

    def __str__(self):
        return f"{self.acao} {self.entidade} {self.registro_id} por {self.usuario_id}"
