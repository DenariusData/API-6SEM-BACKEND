"""Classificador do tipo de documento: texto que a rede lê e a conta da rede."""

import numpy as np
import pymupdf
import pytest

from core_api.services.classificador_tipo import (
    CARACTERES_ENTRADA,
    RedeTipo,
    texto_do_pdf,
    texto_para_classificar,
)

CLASSES = np.array(["Técnica", "Jurídica", "Normativa", "Quantitativa"])


def salvar_rede(caminho, **camadas):
    np.savez(caminho, classes=CLASSES, modelo_embedding="bge-m3", **camadas)
    return caminho


def test_texto_tira_marca_dagua_e_junta_espacos():
    paginas = [
        "MIL-STD-810H\n\n\n   Downloaded from http://www.everyspec.com",
        "ENVIRONMENTAL\tTEST   METHODS",
    ]
    assert texto_para_classificar(paginas) == "MIL-STD-810H ENVIRONMENTAL TEST METHODS"


def test_texto_corta_no_limite():
    assert len(texto_para_classificar(["a " * CARACTERES_ENTRADA])) == (
        CARACTERES_ENTRADA
    )


def test_texto_do_pdf_le_as_primeiras_paginas():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 60), "AIRWORTHINESS DIRECTIVE")
        pdf.new_page().insert_text((40, 60), "Federal Aviation Administration")
        conteudo = pdf.tobytes()
    assert texto_do_pdf(conteudo) == (
        "AIRWORTHINESS DIRECTIVE Federal Aviation Administration"
    )


def test_rede_de_duas_camadas_com_relu_e_softmax(tmp_path):
    # x = [1, -1]; oculta = relu(x @ W0) = [1, 0]; saída = oculta @ W1
    rede = RedeTipo(
        salvar_rede(
            tmp_path / "m.npz",
            W0=np.array([[1.0, 0.0], [0.0, 1.0]]),
            b0=np.zeros(2),
            W1=np.array([[0.0, 0.0, 5.0, 0.0], [9.0, 9.0, 9.0, 9.0]]),
            b1=np.zeros(4),
        )
    )
    p = rede.probabilidades([1.0, -1.0])
    assert rede.dimensao == 2
    assert p.sum() == pytest.approx(1.0)
    assert int(np.argmax(p)) == 2  # o 9 da segunda linha foi zerado pela ReLU
    assert rede.classes[2] == "Normativa"


def test_rede_sem_camadas_e_invalida(tmp_path):
    with pytest.raises(ValueError):
        RedeTipo(salvar_rede(tmp_path / "m.npz"))


def test_saida_precisa_ter_uma_posicao_por_classe(tmp_path):
    with pytest.raises(ValueError):
        RedeTipo(salvar_rede(tmp_path / "m.npz", W0=np.ones((2, 3)), b0=np.zeros(3)))
