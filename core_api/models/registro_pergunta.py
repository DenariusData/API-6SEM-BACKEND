"""
Registro de cada pergunta feita na tela de pergunta (banco de NEGÓCIO).

Serve para acompanhar o desempenho do RAG: quanto tempo levou a busca dos
trechos, quanto levou a IA e se a pergunta tinha base no acervo. Guarda só o
`usuario_id` (uuid), sem FK para `credenciais.Usuario`, que fica em outro banco.

Origem da resposta (AKA-24): junto da pergunta ficam a interpretação da IA,
os modelos que geraram a resposta e, em `FontePergunta`, cada trecho usado,
com documento, revisão, página, seção e uma cópia literal do texto. A cópia
mantém a origem mesmo que o documento seja reprocessado ou apagado depois.
"""

from django.db import models


class RegistroPergunta(models.Model):
    usuario_id = models.UUIDField(null=True, blank=True)
    pergunta = models.TextField()
    idioma = models.CharField(max_length=10, default="pt-BR")
    encontrou = models.BooleanField()
    quantidade_fontes = models.PositiveSmallIntegerField(default=0)
    # Texto que a IA escreveu (ou o aviso de "não encontrei").
    resposta = models.TextField(blank=True, default="")
    modelo_llm = models.CharField(max_length=60, blank=True, default="")
    modelo_embedding = models.CharField(max_length=60, blank=True, default="")
    tempo_busca_ms = models.PositiveIntegerField()
    tempo_ia_ms = models.PositiveIntegerField()
    tempo_total_ms = models.PositiveIntegerField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core_api"
        db_table = "registro_pergunta"
        ordering = ["-criado_em"]
        verbose_name = "Registro de Pergunta"
        verbose_name_plural = "Registros de Perguntas"

    def __str__(self) -> str:
        return f"{self.pergunta[:50]} ({self.tempo_total_ms} ms)"


class FontePergunta(models.Model):
    """Um trecho que embasou a resposta de uma pergunta (AKA-24)."""

    registro = models.ForeignKey(
        RegistroPergunta, on_delete=models.CASCADE, related_name="fontes"
    )
    # Número do trecho na resposta ([1], [2]...).
    numero = models.PositiveSmallIntegerField()
    citada = models.BooleanField(default=False)
    # Documento do sistema; None para a base pública (EverySpec). Sem FK: o
    # registro continua valendo se o documento for apagado.
    documento_id = models.UUIDField(null=True, blank=True)
    # Id do trecho: `chunk.id` (sistema) ou `rag_chunks.id` (base pública).
    trecho_id = models.CharField(max_length=100, null=True, blank=True)
    documento = models.CharField(max_length=300)
    revisao = models.CharField(max_length=100, null=True, blank=True)
    titulo = models.TextField(blank=True, default="")
    pagina_inicio = models.PositiveIntegerField(null=True, blank=True)
    pagina_fim = models.PositiveIntegerField(null=True, blank=True)
    secao = models.CharField(max_length=200, null=True, blank=True)
    similaridade = models.FloatField()
    # Cópia literal do trecho enviado à IA.
    texto = models.TextField()

    class Meta:
        app_label = "core_api"
        db_table = "registro_pergunta_fonte"
        ordering = ["registro_id", "numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["registro", "numero"], name="uq_registro_pergunta_fonte"
            ),
        ]
        verbose_name = "Fonte da Pergunta"
        verbose_name_plural = "Fontes das Perguntas"

    def __str__(self) -> str:
        return f"[{self.numero}] {self.documento}"
