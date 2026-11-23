"""
Referência de integridade dos arquivos enviados (AKA-78).

Guarda o hash e a data de modificação de cada PDF da pasta privada na última
checagem do comando `verificar_integridade_arquivos`. Se muitos arquivos
mudarem ou sumirem de uma vez (sinal de ransomware), o administrador é avisado.
"""

from django.db import models


class IntegridadeArquivo(models.Model):
    documento_id = models.UUIDField(primary_key=True)
    arquivo = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    tamanho = models.BigIntegerField()
    modificado_em = models.FloatField()
    verificado_em = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "core_api"
        db_table = "integridade_arquivo"
        verbose_name = "Integridade de arquivo"
        verbose_name_plural = "Integridade dos arquivos"

    def __str__(self) -> str:
        return f"{self.arquivo} ({self.sha256[:12]})"
