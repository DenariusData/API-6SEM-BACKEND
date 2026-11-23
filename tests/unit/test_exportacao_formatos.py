"""
Formatos da exportação de dados pessoais (AKA-75), sem banco: o JSON e o PDF
são gerados a partir do mesmo dicionário que `coletar` devolve.
"""

import json

import pymupdf

from core_api.services.auth.exportacao_dados import (
    NAO_INCLUIDOS,
    para_json,
    para_pdf,
)

DADOS = {
    "exportacao": {
        "gerada_em": "2026-11-10T15:30:00+00:00",
        "versao_formato": "1.0",
        "base_legal": "LGPD (Lei 13.709/2018), art. 18, II e V, e art. 19",
    },
    "identificacao": {
        "id": "8a6e0804-2bd0-4672-b79d-d97027f9071a",
        "nome": "João Ação <script>",
        "email": "joao@akaer.com.br",
        "email_pendente_confirmacao": None,
        "papel": {"codigo": "ENGENHARIA", "descricao": "Engenharia"},
        "ativo": True,
        "criado_em": "2026-08-01T12:00:00+00:00",
        "ultimo_login_em": None,
    },
    "perfil_operacional": {
        "matricula": "AK-00001",
        "cargo": "Engenheiro",
        "avatar": "/avatars/default.png",
        "menus_liberados": ["Início", "Documentos"],
    },
    "perguntas": [
        {
            "pergunta": "Qual o torque do parafuso?",
            "idioma": "pt-BR",
            "encontrou_resposta": True,
            "quantidade_fontes": 2,
            "feita_em": "2026-09-01T10:00:00+00:00",
        }
    ]
    * 80,  # força mais de uma página
    "documentos": [],
    "historico_validacao": [],
    "acessos_a_documentos": [],
    "registro_de_atividades": [],
    "nao_incluidos": [dict(item) for item in NAO_INCLUIDOS],
}


def _texto_pdf(conteudo):
    with pymupdf.open("pdf", conteudo) as doc:
        # Sem TEXT_PRESERVE_LIGATURES: "fi" volta como duas letras, não "ﬁ".
        flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
        return doc.page_count, "".join(p.get_text(flags=flags) for p in doc)


def test_json_e_utf8_legivel_e_volta_ao_mesmo_dicionario():
    conteudo = para_json(DADOS)

    assert "João Ação".encode() in conteudo  # sem ã escapado
    assert json.loads(conteudo.decode("utf-8")) == DADOS


def test_pdf_valido_com_os_mesmos_dados_e_acentos():
    conteudo = para_pdf(DADOS)

    assert conteudo.startswith(b"%PDF")
    paginas, texto = _texto_pdf(conteudo)
    assert "João Ação <script>" in texto  # escapado no HTML, legível no PDF
    assert "joao@akaer.com.br" in texto
    assert "AK-00001" in texto
    assert "Qual o torque do parafuso?" in texto
    assert "O que não foi incluído e por quê" in texto


def test_pdf_tem_rodape_com_numero_de_paginas():
    paginas, texto = _texto_pdf(para_pdf(DADOS))

    assert paginas > 1
    assert f"Página 1 de {paginas}" in texto
    assert f"Página {paginas} de {paginas}" in texto


def test_pdf_mostra_datas_no_horario_de_brasilia():
    _, texto = _texto_pdf(para_pdf(DADOS))

    # 15:30 UTC = 12:30 em Brasília.
    assert "10/11/2026 12:30" in texto


def test_pdf_sem_perfil_e_listas_vazias():
    dados = {**DADOS, "perfil_operacional": None, "perguntas": []}

    _, texto = _texto_pdf(para_pdf(dados))

    assert "Sem perfil operacional cadastrado." in texto
    assert "Nenhuma pergunta registrada." in texto
