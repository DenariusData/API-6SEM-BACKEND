"""
Sugestão do tipo de documento no upload: uma rede neural simples que lê o
começo do PDF e diz se ele é Técnica, Jurídica, Normativa ou Quantitativa.

    texto_do_pdf(conteudo)  texto que a rede lê: as primeiras páginas, limpas
                            e cortadas em CARACTERES_ENTRADA. O coletor de
                            amostras (classificador/coletar_amostras.py) usa
                            esta mesma função: treino e uso leem igual.
    sugerir_tipo(arquivo)   {"tipo_documento", "confianca", "probabilidades"}
                            ou SemSugestao(motivo).

Como funciona:
    PDF -> texto (pymupdf) -> vetor de 1024 números (bge-m3 no Ollama local,
    o mesmo modelo do RAG) -> rede neural 1024 -> 64 -> 4 -> probabilidade
    de cada tipo (softmax).

A rede é treinada no Colab (classificador/treino_classificador.ipynb), com
os documentos que a equipe rotulou, e chega aqui como um arquivo .npz com os
pesos de cada camada (W0, b0, W1, b1, ...). A conta é feita com numpy: o
servidor não precisa de TensorFlow.

Sem o arquivo do modelo, sem texto no PDF ou com o Ollama fora do ar, não há
sugestão: o formulário segue como antes e o usuário escolhe o tipo. A
sugestão nunca grava nada; quem decide o tipo é o usuário, no upload.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pymupdf
from django.conf import settings

from core_api.management.commands import extrair_texto, gerar_embeddings
from core_api.management.commands.testar_bge_m3_local import validar_ollama_local

logger = logging.getLogger(__name__)

# O treino e o uso precisam ler o mesmo pedaço do documento. Mudou aqui,
# rode o coletor de novo e retreine a rede.
CARACTERES_ENTRADA = 4000
# Menos texto que isso (PDF digitalizado sem OCR, capa só com imagem): a
# rede chutaria, então não sugere.
CARACTERES_MINIMOS = 200
# Abaixo disso a rede está em dúvida: devolve as probabilidades, mas sem
# tipo sugerido, e o formulário não pré-preenche.
CONFIANCA_MINIMA = 0.5


class SemSugestao(Exception):
    """Não há sugestão para este arquivo; a mensagem diz o porquê."""


# -- texto ----------------------------------------------------------------
def texto_para_classificar(paginas) -> str:
    """Junta o texto das páginas (já extraído), tira a marca d'água do
    EverySpec, junta os espaços e corta em CARACTERES_ENTRADA."""
    texto = " ".join(extrair_texto.limpar(pagina) for pagina in paginas)
    return re.sub(r"\s+", " ", texto).strip()[:CARACTERES_ENTRADA]


def texto_do_pdf(conteudo) -> str:
    """Texto que a rede lê. `conteudo` são os bytes do PDF ou um caminho."""
    if isinstance(conteudo, (bytes, bytearray)):
        documento = pymupdf.open(stream=bytes(conteudo), filetype="pdf")
    else:
        documento = pymupdf.open(Path(conteudo))
    paginas, lidos = [], 0
    with documento:
        for pagina in documento:
            texto = pagina.get_text("text")
            paginas.append(texto)
            lidos += len(texto)
            # Folga para os espaços que a limpeza tira.
            if lidos >= CARACTERES_ENTRADA * 2:
                break
    return texto_para_classificar(paginas)


# -- rede -----------------------------------------------------------------
class RedeTipo:
    """Rede densa salva pelo notebook: camadas (W, b) com ReLU entre elas e
    softmax na saída."""

    def __init__(self, caminho):
        with np.load(caminho, allow_pickle=False) as dados:
            total = sum(1 for chave in dados.files if re.fullmatch(r"W\d+", chave))
            if total == 0:
                raise ValueError("o arquivo não tem as camadas W0, b0, ...")
            self.camadas = [
                (
                    dados[f"W{i}"].astype(np.float32),
                    dados[f"b{i}"].astype(np.float32),
                )
                for i in range(total)
            ]
            self.classes = [str(classe) for classe in dados["classes"]]
            self.modelo_embedding = (
                str(dados["modelo_embedding"])
                if "modelo_embedding" in dados.files
                else "bge-m3"
            )
        saida = self.camadas[-1][0].shape[1]
        if saida != len(self.classes):
            raise ValueError(
                f"a última camada tem {saida} saídas e há {len(self.classes)} classes"
            )

    @property
    def dimensao(self) -> int:
        return self.camadas[0][0].shape[0]

    def probabilidades(self, vetor) -> np.ndarray:
        x = np.asarray(vetor, dtype=np.float32)
        for i, (pesos, vies) in enumerate(self.camadas):
            x = x @ pesos + vies
            if i < len(self.camadas) - 1:
                x = np.maximum(x, 0)  # ReLU
        x = np.exp(x - x.max())  # softmax estável
        return x / x.sum()


def caminho_do_modelo() -> Path:
    return Path(
        getattr(
            settings,
            "CLASSIFICADOR_TIPO_MODELO",
            Path(settings.BASE_DIR) / "classificador" / "modelo_tipo_documento.npz",
        )
    )


@lru_cache(maxsize=2)
def _carregar(caminho: str, modificado_em: float) -> RedeTipo:
    # A data de modificação entra na chave: trocar o .npz por um retreinado
    # vale na próxima sugestão, sem reiniciar o servidor.
    return RedeTipo(caminho)


def rede() -> RedeTipo:
    caminho = caminho_do_modelo()
    if not caminho.is_file():
        raise SemSugestao("O classificador ainda não foi treinado neste servidor.")
    try:
        return _carregar(str(caminho), caminho.stat().st_mtime)
    except (OSError, KeyError, ValueError) as e:
        logger.exception("Arquivo do classificador de tipo inválido: %s", caminho)
        raise SemSugestao("O arquivo do classificador está inválido.") from e


# -- sugestão -------------------------------------------------------------
def sugerir_tipo(arquivo) -> dict:
    """Sugestão para o PDF enviado (UploadedFile já validado). Levanta
    SemSugestao quando não há o que sugerir."""
    modelo = rede()

    arquivo.seek(0)
    conteudo = arquivo.read()
    arquivo.seek(0)
    try:
        texto = texto_do_pdf(conteudo)
    except Exception as e:  # noqa: BLE001 - PDF corrompido, protegido etc.
        raise SemSugestao("Não foi possível ler o texto do PDF.") from e
    if len(texto) < CARACTERES_MINIMOS:
        raise SemSugestao("O PDF quase não tem texto legível (digitalizado sem OCR?).")

    try:
        validar_ollama_local(gerar_embeddings.OLLAMA_URL)
        [vetor] = gerar_embeddings.gerar_vetores([texto], modelo.modelo_embedding)
    except (ValueError, gerar_embeddings.ErroOllama) as e:
        logger.warning("Sugestão de tipo sem embedding: %s", e)
        raise SemSugestao("A IA está indisponível agora.") from e
    if len(vetor) != modelo.dimensao:
        raise SemSugestao(
            f"O modelo {modelo.modelo_embedding} devolveu {len(vetor)} números; "
            f"a rede espera {modelo.dimensao}."
        )

    # Import aqui: o coletor de amostras usa texto_do_pdf sem subir o Django.
    from core_api.models import TipoDocumento

    probabilidades = modelo.probabilidades(vetor)
    melhor = int(np.argmax(probabilidades))
    confianca = float(probabilidades[melhor])
    tipo = (
        TipoDocumento.objects.filter(ativo=True, nome=modelo.classes[melhor]).first()
        if confianca >= CONFIANCA_MINIMA
        else None
    )
    return {
        "tipo_documento": tipo and {"id": tipo.id, "nome": tipo.nome},
        "confianca": round(confianca, 3),
        "probabilidades": {
            classe: round(float(p), 3)
            for classe, p in zip(modelo.classes, probabilidades)
        },
    }
