"""
Verifica a integridade dos PDFs da pasta privada (AKA-78).

Compara o hash (SHA-256) de cada arquivo de documento com o guardado na
checagem anterior (tabela integridade_arquivo). Se mais de
ALERTA_INTEGRIDADE_PERCENTUAL % dos arquivos mudaram ou sumiram desde a última
vez, manda o alerta ARQUIVOS_ALTERADOS (possível ransomware). Depois grava a
situação atual como a nova referência.

Na primeira execução só grava a referência. Agendar no deploy, por exemplo a
cada hora no cron:

    0 * * * * cd /caminho/do/back && venv/bin/python manage.py verificar_integridade_arquivos

Uso:
    python manage.py verificar_integridade_arquivos
"""

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core_api.models import Documento, IntegridadeArquivo
from core_api.services import alertas

BLOCO = 1024 * 1024


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(BLOCO):
            resumo.update(bloco)
    return resumo.hexdigest()


def _caminho(documento) -> Path | None:
    referencia = (documento.arquivo_original_url or "").strip()
    if not referencia or "://" in referencia:
        return None
    pasta = Path(settings.DOCUMENTOS_UPLOAD_DIR).resolve()
    return pasta / Path(referencia).name


def verificar() -> dict:
    """Faz a checagem e devolve o resumo (usado pelo comando e pelos testes)."""
    referencias = {r.documento_id: r for r in IntegridadeArquivo.objects.all()}
    alterados, sumidos, conferidos = [], [], 0

    for documento in Documento.objects.only("id", "arquivo_original_url"):
        caminho = _caminho(documento)
        if caminho is None:
            continue
        anterior = referencias.get(documento.id)
        if not caminho.is_file():
            if anterior is not None:
                sumidos.append(documento.id)
                conferidos += 1
            continue
        estado = caminho.stat()
        sha = _sha256(caminho)
        if anterior is not None:
            conferidos += 1
            if anterior.sha256 != sha:
                alterados.append(documento.id)
        IntegridadeArquivo.objects.update_or_create(
            documento_id=documento.id,
            defaults={
                "arquivo": caminho.name,
                "sha256": sha,
                "tamanho": estado.st_size,
                "modificado_em": estado.st_mtime,
            },
        )

    afetados = len(alterados) + len(sumidos)
    percentual = (afetados / conferidos * 100) if conferidos else 0.0
    alertou = percentual > settings.ALERTA_INTEGRIDADE_PERCENTUAL and afetados > 0
    if alertou:
        alertas.enviar_alerta(
            alertas.ARQUIVOS_ALTERADOS,
            None,
            {
                "Arquivos conferidos": conferidos,
                "Alterados": len(alterados),
                "Removidos": len(sumidos),
                "Percentual afetado": f"{percentual:.0f}%",
            },
        )
    return {
        "conferidos": conferidos,
        "alterados": len(alterados),
        "sumidos": len(sumidos),
        "percentual": percentual,
        "alertou": alertou,
    }


class Command(BaseCommand):
    help = (
        "Confere o hash dos PDFs e avisa o administrador se muitos mudarem de uma vez."
    )

    def handle(self, *args, **options):
        resumo = verificar()
        self.stdout.write(
            f"Conferidos: {resumo['conferidos']} · alterados: {resumo['alterados']} · "
            f"removidos: {resumo['sumidos']} · {resumo['percentual']:.0f}%"
        )
        if resumo["alertou"]:
            self.stdout.write(self.style.WARNING("Alerta enviado ao administrador."))
