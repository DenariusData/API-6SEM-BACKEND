"""
AKA-24: origem de cada resposta da IA.

- `chunk.secao`: seção do documento onde o trecho começa. Os trechos que já
  existem são preenchidos aqui, documento por documento. A regra é uma cópia
  congelada de core_api/services/rag/secoes.py: se a regra do processamento
  mudar depois, esta migration continua fazendo o mesmo.
- `registro_pergunta`: resposta da IA e modelos usados.
- `registro_pergunta_fonte`: cada trecho que embasou a resposta, com cópia
  literal do texto.
"""

import re

import django.db.models.deletion
from django.db import migrations, models

# ---- Cópia congelada de core_api/services/rag/secoes.py ----

TAMANHO_MAXIMO_TITULO = 100

# "5. Título", "4.1.2 Título", "SECTION 3 - TÍTULO", "APPENDIX A", "ANEXO B"
RE_TITULO = re.compile(
    r"""^(?P<titulo>
        (?:
            (?:SECTION|SEÇÃO|SECAO|CHAPTER|CAPÍTULO|CAPITULO|APPENDIX|ANNEX
              |ANEXO|APÊNDICE|APENDICE)\s+[0-9A-Z]{1,3}(?:\.\d{1,3})*\.?
            (?:\s*[-–—:]?\s+\S.*)?
          |
            \d{1,2}(?:\.\d{1,3}){0,4}\.?\s+\S.*
        )
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
# Título na mesma linha do texto, comum em normas militares:
# "4.1 General. The marking shall be..." -> "4.1 General"
RE_TITULO_EM_LINHA = re.compile(
    r"^(?P<titulo>\d{1,2}(?:\.\d{1,3}){1,4}\.?\s+[^\W\d][^.]{0,60})\.\s+\S"
)
# Linha de sumário: pontilhado, ou número de página separado por vários
# espaços no fim ("3.2 Marking of containers      12").
RE_SUMARIO = re.compile(r"\.{4,}|\s{2,}\d{1,4}\s*$")


def titulo_de_secao(linha):
    """O título de seção que a linha começa, ou None se ela não é título."""
    if RE_SUMARIO.search(linha):
        return None
    linha = " ".join(linha.split())
    if not linha:
        return None

    em_linha = RE_TITULO_EM_LINHA.match(linha)
    if em_linha:
        linha = em_linha.group("titulo")
    elif len(linha) > TAMANHO_MAXIMO_TITULO or not RE_TITULO.match(linha):
        return None
    # Frase de lista ou de texto, não título: termina com pontuação de frase.
    elif linha.endswith((".", ";", ",", ":", "?", "!")) and not _so_rotulo(linha):
        return None
    texto = re.sub(r"^[\d.\s]+", "", linha)
    # Depois do número, o título começa com maiúscula e tem palavra de verdade:
    # descarta medida ("10 mm") e linha quebrada do PDF ("20 peças por lote").
    if linha[0].isdigit() and not texto[:1].isupper():
        return None
    if not re.search(r"[^\W\d_]{3,}", texto) and not _so_rotulo(linha + "."):
        return None
    return linha.rstrip(". ")


def _so_rotulo(linha):
    """ "APPENDIX A." ou "SECTION 3." sem título: o ponto final não é de frase."""
    return bool(re.fullmatch(r"[^\W\d_]+\s+[0-9A-Z]{1,3}(?:\.\d{1,3})*\.", linha))


def _linhas(texto):
    return [linha.strip() for linha in texto.split("\n") if linha.strip()]


def _sobreposicao(anteriores, atuais):
    """Quantas linhas do início do trecho atual repetem o fim do anterior."""
    for k in range(min(len(anteriores) - 1, len(atuais)), 0, -1):
        if anteriores[-k:] == atuais[:k]:
            return k
    return 0


def secao_no_texto(texto):
    """Primeiro título de seção dentro do texto (trecho avulso, sem os
    vizinhos, como os da base pública)."""
    for linha in _linhas(texto):
        titulo = titulo_de_secao(linha)
        if titulo:
            return titulo
    return None


def secoes_dos_trechos(textos):
    """Seção de cada trecho de um documento, na ordem dos trechos.

    Para cada linha guarda o título que vale depois dela; o início do trecho
    seguinte herda o título que valia antes da parte repetida (sobreposição).
    Trecho do começo do documento, antes de qualquer título, fica com o
    primeiro título que aparecer nele, ou None.
    """
    secoes = []
    linhas_anteriores, vigor_anterior = [], []
    for texto in textos:
        linhas = _linhas(texto)
        # Última linha do trecho anterior que não se repete no início deste.
        corte = len(linhas_anteriores) - _sobreposicao(linhas_anteriores, linhas) - 1
        vigor_no_inicio = vigor_anterior[corte] if corte >= 0 else None

        vigor, atual = [], vigor_no_inicio
        for linha in linhas:
            atual = titulo_de_secao(linha) or atual
            vigor.append(atual)

        secao = vigor[0] if vigor else vigor_no_inicio
        secoes.append(secao or secao_no_texto(texto))
        linhas_anteriores, vigor_anterior = linhas, vigor
    return secoes


# ---- Fim da cópia ----


def preencher_secoes(apps, schema_editor):
    Chunk = apps.get_model("core_api", "Chunk")
    documentos = Chunk.objects.values_list("documento_id", flat=True).distinct()
    for documento_id in documentos:
        # Um documento por vez e só id e texto: a memória fica no tamanho do
        # maior documento, não do acervo inteiro.
        chunks = list(
            Chunk.objects.filter(documento_id=documento_id)
            .order_by("ordem")
            .only("id", "texto")
        )
        for chunk, secao in zip(chunks, secoes_dos_trechos([c.texto for c in chunks])):
            chunk.secao = secao
        Chunk.objects.bulk_update(chunks, ["secao"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("core_api", "0010_log_imutavel"),
    ]

    operations = [
        migrations.AddField(
            model_name="chunk",
            name="secao",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="registropergunta",
            name="modelo_embedding",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="registropergunta",
            name="modelo_llm",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
        migrations.AddField(
            model_name="registropergunta",
            name="resposta",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="FontePergunta",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("numero", models.PositiveSmallIntegerField()),
                ("citada", models.BooleanField(default=False)),
                ("documento_id", models.UUIDField(blank=True, null=True)),
                ("trecho_id", models.CharField(blank=True, max_length=100, null=True)),
                ("documento", models.CharField(max_length=300)),
                ("revisao", models.CharField(blank=True, max_length=100, null=True)),
                ("titulo", models.TextField(blank=True, default="")),
                ("pagina_inicio", models.PositiveIntegerField(blank=True, null=True)),
                ("pagina_fim", models.PositiveIntegerField(blank=True, null=True)),
                ("secao", models.CharField(blank=True, max_length=200, null=True)),
                ("similaridade", models.FloatField()),
                ("texto", models.TextField()),
                (
                    "registro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fontes",
                        to="core_api.registropergunta",
                    ),
                ),
            ],
            options={
                "verbose_name": "Fonte da Pergunta",
                "verbose_name_plural": "Fontes das Perguntas",
                "db_table": "registro_pergunta_fonte",
                "ordering": ["registro_id", "numero"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("registro", "numero"), name="uq_registro_pergunta_fonte"
                    )
                ],
            },
        ),
        migrations.RunPython(preencher_secoes, migrations.RunPython.noop),
    ]
