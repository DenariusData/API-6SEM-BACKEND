"""
Visualização segura do documento (AKA-48 / S2-43).

O navegador nunca recebe o PDF original: cada página é convertida em PNG
(PyMuPDF, ~150 dpi) e recebe, na hora da resposta, uma marca d'água com o
nome e o e-mail de quem está vendo, a data/hora e o identificador do
documento.

  - Só PDF nesta sprint (AKA-59). Outro formato -> FormatoNaoSuportado (415).
  - O arquivo é lido só da pasta privada do upload (DOCUMENTOS_UPLOAD_DIR),
    pelo nome gravado em `arquivo_original_url`; nada de ../ nem URL.
  - Cache só da página SEM marca (a marca muda por usuário e por horário).
  - Quem pode ver é a mesma regra do detalhe (consulta_documentos.pode_ver).
"""

import io
import math
from functools import lru_cache
from pathlib import Path

import pymupdf
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

from core_api.models import AcaoAcesso, LogAcesso, PerfilOperacional

DPI = 150
OPACIDADE_MARCA = 51  # ~20% de 255
COR_MARCA = (90, 90, 90)
TAMANHO_FONTE_MARCA = 28
ANGULO_MARCA = 30  # graus, diagonal
MAGIC_PDF = b"%PDF"


class FormatoNaoSuportado(Exception):
    """O arquivo do documento não é PDF (HTTP 415)."""


class ArquivoIndisponivel(Exception):
    """O arquivo do documento não está na pasta privada (HTTP 404)."""


# -- arquivo ------------------------------------------------------------
def _pasta_privada() -> Path:
    return Path(settings.DOCUMENTOS_UPLOAD_DIR).resolve()


def caminho_do_pdf(documento) -> Path:
    """Caminho do PDF na pasta privada, ou exceção.

    Só o nome do arquivo é usado (Path(...).name), então um valor como
    "../../settings.py" vira "settings.py" dentro da pasta privada. URL nunca
    é seguida. A extensão e os primeiros bytes precisam ser de PDF.
    """
    referencia = (documento.arquivo_original_url or "").strip()
    if not referencia or "://" in referencia:
        raise ArquivoIndisponivel("Arquivo do documento não encontrado no servidor.")

    pasta = _pasta_privada()
    caminho = (pasta / Path(referencia).name).resolve()
    if caminho.parent != pasta:
        raise ArquivoIndisponivel("Arquivo do documento não encontrado no servidor.")

    if caminho.suffix.lower() != ".pdf":
        raise FormatoNaoSuportado("Visualização disponível apenas para PDF")
    if not caminho.is_file():
        raise ArquivoIndisponivel("Arquivo do documento não encontrado no servidor.")
    with caminho.open("rb") as arquivo:
        if arquivo.read(len(MAGIC_PDF)) != MAGIC_PDF:
            raise FormatoNaoSuportado("Visualização disponível apenas para PDF")
    return caminho


def _versao(caminho: Path) -> int:
    """Muda se o arquivo for trocado; entra na chave do cache."""
    return caminho.stat().st_mtime_ns


@lru_cache(maxsize=64)
def _total_paginas(caminho_str: str, versao: int) -> int:
    with pymupdf.open(caminho_str) as pdf:
        return pdf.page_count


def total_paginas(caminho: Path) -> int:
    return _total_paginas(str(caminho), _versao(caminho))


@lru_cache(maxsize=128)
def _pagina_sem_marca(caminho_str: str, versao: int, numero: int) -> bytes:
    """PNG da página `numero` (1-based), SEM marca d'água. É o que fica em
    cache: a marca é aplicada a cada resposta."""
    with pymupdf.open(caminho_str) as pdf:
        pixmap = pdf[numero - 1].get_pixmap(dpi=DPI)
        return pixmap.tobytes("png")


def pagina_sem_marca(caminho: Path, numero: int) -> bytes:
    return _pagina_sem_marca(str(caminho), _versao(caminho), numero)


# -- marca d'água ---------------------------------------------------------
def texto_da_marca(nome, email, quando, identificador) -> str:
    """'nome - e-mail - dd/mm/aaaa hh:mm - identificador'."""
    partes = [nome or "", email or "", quando.strftime("%d/%m/%Y %H:%M"), identificador]
    return " - ".join(p for p in partes if p)


@lru_cache(maxsize=1)
def _fonte():
    """Helvetica (Nimbus Sans) que já vem dentro do PyMuPDF: tem acentos (ã, ç, é...) e
    funciona igual em Windows e Linux, sem depender de fonte instalada.
    A fonte padrão do Pillow NÃO tem acentos ("João" sairia "Jo□o")."""
    try:
        return ImageFont.truetype(
            io.BytesIO(pymupdf.Font("helv").buffer), TAMANHO_FONTE_MARCA
        )
    except (OSError, ValueError, RuntimeError):
        return ImageFont.load_default(size=TAMANHO_FONTE_MARCA)


def aplicar_marca_dagua(imagem: Image.Image, texto: str) -> Image.Image:
    """Devolve uma cópia da imagem com `texto` repetido na diagonal pela
    página inteira, em cinza com ~20% de opacidade (texto continua legível)."""
    base = imagem.convert("RGBA")
    largura, altura = base.size
    fonte = _fonte()

    # Desenha numa camada maior que a página, gira e recorta no centro: assim a
    # diagonal cobre os cantos.
    lado = int(math.hypot(largura, altura)) + 1
    camada = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(camada)

    linha = f"{texto}     "
    x0, y0, x1, y1 = desenho.textbbox((0, 0), linha, font=fonte)
    largura_texto, altura_texto = max(x1 - x0, 1), max(y1 - y0, 1)
    passo_y = altura_texto * 4
    cor = (*COR_MARCA, OPACIDADE_MARCA)

    for i, y in enumerate(range(0, lado, passo_y)):
        deslocamento = (i % 2) * (largura_texto // 2)
        for x in range(-deslocamento, lado, largura_texto):
            desenho.text((x, y), linha, font=fonte, fill=cor)

    camada = camada.rotate(ANGULO_MARCA, resample=Image.BICUBIC)
    esquerda, topo = (lado - largura) // 2, (lado - altura) // 2
    camada = camada.crop((esquerda, topo, esquerda + largura, topo + altura))
    return Image.alpha_composite(base, camada).convert("RGB")


def pagina_com_marca(caminho: Path, numero: int, texto: str) -> bytes:
    imagem = Image.open(io.BytesIO(pagina_sem_marca(caminho, numero)))
    marcada = aplicar_marca_dagua(imagem, texto)
    saida = io.BytesIO()
    marcada.save(saida, format="PNG", optimize=True)
    return saida.getvalue()


# -- log ------------------------------------------------------------------
def ip_de(request):
    """IP de origem. Usa REMOTE_ADDR: X-Forwarded-For pode ser forjado pelo
    cliente e não há proxy reverso configurado no projeto."""
    return request.META.get("REMOTE_ADDR") or None


def registrar_visualizacao(documento, usuario_id, ip) -> bool:
    """Grava VISUALIZACAO no LogAcesso. Devolve False se o usuário não tem
    PerfilOperacional (aí a view responde 403, como nos endpoints de validação)."""
    perfil = PerfilOperacional.objects.filter(pk=usuario_id).first()
    if perfil is None:
        return False
    LogAcesso.objects.create(
        documento=documento,
        usuario=perfil,
        acao=AcaoAcesso.VISUALIZACAO,
        ip_origem=ip,
    )
    return True
