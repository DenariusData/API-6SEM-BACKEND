"""
Registro da pergunta com a origem da resposta (AKA-24).

Grava, numa transação, a pergunta com a interpretação da IA e os modelos que
a geraram (`RegistroPergunta`) e cada trecho usado, com cópia literal do
texto (`FontePergunta`). Assim dá para saber depois de onde veio cada
resposta, mesmo que o documento seja reprocessado ou apagado.
"""

from django.db import transaction

from core_api.models import FontePergunta, RegistroPergunta


def registrar_pergunta(usuario_id, pergunta, idioma, resultado):
    """Grava a pergunta e a origem da resposta devolvida por
    `pergunta.responder_pergunta`. Devolve o `RegistroPergunta`."""
    tempos = resultado["tempos_ms"]
    modelos = resultado["modelos"]
    textos = {t["numero"]: t["texto"] for t in resultado["trechos_extraidos"]}
    with transaction.atomic():
        registro = RegistroPergunta.objects.create(
            usuario_id=usuario_id,
            pergunta=pergunta,
            idioma=idioma,
            encontrou=resultado["encontrou"],
            quantidade_fontes=len(resultado["fontes"]),
            resposta=resultado["interpretacao_ia"],
            modelo_llm=modelos["llm"] or "",
            modelo_embedding=modelos["embedding"],
            tempo_busca_ms=tempos["busca"],
            tempo_ia_ms=tempos["ia"],
            tempo_total_ms=tempos["total"],
        )
        FontePergunta.objects.bulk_create(
            FontePergunta(
                registro=registro,
                numero=fonte["numero"],
                citada=fonte["citada"],
                documento_id=fonte["documento_id"],
                trecho_id=fonte["trecho_id"],
                documento=fonte["documento"],
                revisao=fonte["revisao"],
                titulo=fonte["titulo"] or "",
                pagina_inicio=fonte["pagina_inicio"],
                pagina_fim=fonte["pagina_fim"],
                secao=fonte["secao"],
                similaridade=fonte["similaridade"],
                texto=textos[fonte["numero"]],
            )
            for fonte in resultado["fontes"]
        )
    return registro
