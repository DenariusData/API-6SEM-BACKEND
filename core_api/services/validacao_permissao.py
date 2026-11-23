"""
Validação de demonstração do filtro de permissão (S2-26).

A MESMA pergunta, feita como dois usuários demo (Engenharia e Qualidade), tem
de trazer fontes diferentes e nenhum trecho fora da permissão de cada um.

Como a validação não se engana sozinha:
  - o fluxo testado é o real (`responder_pergunta`: embedding, busca com filtro,
    IA), contra o banco e o Ollama de quem roda;
  - a conferência NÃO reutiliza a query de permissão. A regra é reescrita aqui
    em Python, direto das tabelas `usuario_permissao_area`, `documento_area` e
    `nivel_sigilo` (`pode_acessar`). Se a query tiver bug, as duas respostas
    divergem e a validação reprova;
  - uma busca de CONTROLE, sem filtro, mostra o que o filtro tirou: os trechos
    de documentos do sistema que seriam usados se não houvesse permissão;
  - a base pública do EverySpec não tem sigilo e vale para todos: aparece
    separada e não conta como "fonte diferente".

A busca de controle é só leitura, para este relatório. Não existe (e não deve
existir) um parâmetro que desligue o filtro na busca da IA.

Resultado de cada critério: True (passou), False (reprovou) ou None
(inconclusivo: o acervo não permite demonstrar, ex.: nenhum documento do
sistema entre as fontes).
"""

import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils import timezone
from pgvector.django import CosineDistance

from core_api.models import (
    Documento,
    DocumentoArea,
    EmbeddingVersion,
    UsuarioPermissaoArea,
)
from core_api.services.rag import pergunta as servico_pergunta
from credenciais.models import Usuario

EMAILS_DEMO = ("engenharia@akaer.com.br", "qualidade@akaer.com.br")
FUSO = ZoneInfo("America/Sao_Paulo")
LIMITE_CONTROLE = 40  # candidatos da busca sem filtro (limite do índice HNSW)
LIMITE_BLOQUEADOS = 10  # quantos trechos bloqueados mostrar por usuário

ORIGEM_SISTEMA = "Documento do sistema"
ORIGEM_PUBLICA = "Base pública (EverySpec)"


class UsuarioDemoInexistente(Exception):
    """Falta o usuário demo no banco (rode seed_usuarios_demo)."""


# -- dados ------------------------------------------------------------------
def resolver_usuarios(emails=EMAILS_DEMO):
    """[{email, nome, usuario_id}] na ordem pedida, lidos de credenciais_db."""
    achados = {
        u.email: u
        for u in Usuario.objects.using("credenciais_db").filter(email__in=emails)
    }
    faltando = [e for e in emails if e not in achados]
    if faltando:
        raise UsuarioDemoInexistente(
            "Usuário(s) não encontrado(s): "
            + ", ".join(faltando)
            + ". Rode seed_taxonomia e seed_usuarios_demo."
        )
    return [
        {"email": e, "nome": achados[e].nome, "usuario_id": achados[e].id}
        for e in emails
    ]


def permissoes_de(usuario_id):
    linhas = (
        UsuarioPermissaoArea.objects.filter(usuario_id=usuario_id)
        .select_related("area", "nivel_sigilo_maximo")
        .order_by("area__nome")
    )
    return [
        {
            "area_id": p.area_id,
            "area": p.area.nome,
            "nivel": p.nivel_sigilo_maximo.nome,
            "ordinal": p.nivel_sigilo_maximo.ordinal,
        }
        for p in linhas
    ]


def inventario(modelo):
    """Documentos DISPONÍVEIS, por id (str): código, título, sigilo, áreas e
    quantos trechos com embedding vigente do `modelo` eles têm."""
    docs = list(Documento.objects.disponiveis().select_related("nivel_sigilo"))
    por_id = {
        str(d.id): {
            "id": str(d.id),
            "codigo": d.identificador_codigo,
            "revisao": d.numero_revisao or None,
            "titulo": d.titulo,
            "sigilo": d.nivel_sigilo.nome,
            "ordinal": d.nivel_sigilo.ordinal,
            "areas": [],
            "trechos": 0,
        }
        for d in docs
    }
    ligacoes = DocumentoArea.objects.filter(documento__in=docs).select_related("area")
    for ligacao in ligacoes.order_by("-is_principal", "area__nome"):
        por_id[str(ligacao.documento_id)]["areas"].append(
            {
                "id": ligacao.area_id,
                "nome": ligacao.area.nome,
                "principal": ligacao.is_principal,
            }
        )
    contagem = (
        EmbeddingVersion.objects.filter(
            vigente=True, modelo=modelo, chunk__documento__in=docs
        )
        .order_by()
        .values_list("chunk__documento_id")
        .annotate(n=Count("id"))
    )
    for documento_id, n in contagem:
        por_id[str(documento_id)]["trechos"] = n
    return por_id


# -- a regra, reescrita por fora da query ----------------------------------
def pode_acessar(doc, permissoes):
    """Alguma área do documento em que o usuário tem permissão, com nível de
    sigilo do documento até o máximo concedido NAQUELA área. `doc` é um item
    de `inventario` (só documentos disponíveis)."""
    maximo = {p["area_id"]: p["ordinal"] for p in permissoes}
    return any(
        a["id"] in maximo and maximo[a["id"]] >= doc["ordinal"] for a in doc["areas"]
    )


def motivo_do_bloqueio(doc, permissoes):
    maximo = {p["area_id"]: p for p in permissoes}
    motivos = []
    for area in doc["areas"]:
        regra = maximo.get(area["id"])
        if regra is None:
            motivos.append(f"sem permissão em {area['nome']}")
        else:
            motivos.append(
                f"sigilo {doc['sigilo']} acima do máximo {regra['nivel']} "
                f"em {area['nome']}"
            )
    return "; ".join(motivos) or "documento sem área"


# -- coleta -------------------------------------------------------------------
def candidatos_sem_filtro(vetor, modelo):
    """Trechos de documentos disponíveis, do mais ao menos parecido, SEM a
    regra de permissão. Só para o relatório de controle."""
    embeddings = (
        EmbeddingVersion.objects.filter(
            vigente=True,
            modelo=modelo,
            chunk__documento__in=Documento.objects.disponiveis(),
        )
        .select_related("chunk")
        .annotate(distancia=CosineDistance("embedding", vetor))
        .order_by("distancia")[:LIMITE_CONTROLE]
    )
    return [
        {
            "documento_id": str(e.chunk.documento_id),
            "trecho_id": str(e.chunk_id),
            "pagina_inicio": e.chunk.pagina_inicio,
            "pagina_fim": e.chunk.pagina_fim,
            "similaridade": 1 - float(e.distancia),
        }
        for e in embeddings
    ]


def _descrever_fonte(fonte, inv, permissoes):
    documento_id = fonte["documento_id"]
    base = {
        "numero": fonte["numero"],
        "documento_id": documento_id,
        "trecho_id": fonte["trecho_id"],
        "codigo": fonte["documento"],
        "titulo": fonte["titulo"],
        "pagina": fonte["pagina"],
        "similaridade": fonte["similaridade"],
        "citada": fonte["citada"],
    }
    if documento_id is None:
        return {
            **base,
            "origem": ORIGEM_PUBLICA,
            "areas": [],
            "sigilo": "sem sigilo",
            "permitida": None,
        }
    doc = inv.get(documento_id)
    return {
        **base,
        "origem": ORIGEM_SISTEMA,
        "areas": [a["nome"] for a in doc["areas"]] if doc else [],
        "sigilo": doc["sigilo"] if doc else "?",
        # Documento que não está disponível nem entra na conta: vazamento.
        "permitida": bool(doc) and pode_acessar(doc, permissoes),
    }


def _descrever_bloqueados(candidatos, inv, permissoes):
    minima = servico_pergunta.SIMILARIDADE_MINIMA
    bloqueados = []
    for c in candidatos:
        doc = inv.get(c["documento_id"])
        if doc is None or c["similaridade"] < minima:
            continue
        if pode_acessar(doc, permissoes):
            continue
        p1, p2 = c["pagina_inicio"], c["pagina_fim"]
        bloqueados.append(
            {
                "documento_id": doc["id"],
                "trecho_id": c["trecho_id"],
                "codigo": doc["codigo"],
                "titulo": doc["titulo"],
                "areas": [a["nome"] for a in doc["areas"]],
                "sigilo": doc["sigilo"],
                "pagina": servico_pergunta.formatar_paginas(p1, p2),
                "similaridade": round(c["similaridade"], 3),
                "motivo": motivo_do_bloqueio(doc, permissoes),
            }
        )
    return bloqueados[:LIMITE_BLOQUEADOS]


def coletar(pergunta, idioma, usuarios):
    """Faz a pergunta como cada usuário (fluxo real) e monta os dados do
    relatório. Levanta servico_pergunta.ErroIA se o Ollama/banco não responderem."""
    modelo = servico_pergunta.MODELO_EMBEDDING
    inv = inventario(modelo)
    vetor = servico_pergunta._ollama(
        "/api/embed", {"model": modelo, "input": [pergunta]}
    )["embeddings"][0]
    candidatos = candidatos_sem_filtro(vetor, modelo)

    resultados = []
    for usuario in usuarios:
        permissoes = permissoes_de(usuario["usuario_id"])
        resposta = servico_pergunta.responder_pergunta(
            pergunta, idioma, usuario_id=usuario["usuario_id"]
        )
        resultados.append(
            {
                "email": usuario["email"],
                "nome": usuario["nome"],
                "usuario_id": str(usuario["usuario_id"]),
                "permissoes": permissoes,
                "visiveis": sorted(
                    d["codigo"] for d in inv.values() if pode_acessar(d, permissoes)
                ),
                "encontrou": resposta["encontrou"],
                "resposta": resposta["resposta"],
                "modelos": resposta["modelos"],
                "tempos_ms": resposta["tempos_ms"],
                "fontes": [
                    _descrever_fonte(f, inv, permissoes) for f in resposta["fontes"]
                ],
                "bloqueados": _descrever_bloqueados(candidatos, inv, permissoes),
            }
        )
    dados = {
        "gerado_em": timezone.now().astimezone(FUSO).strftime("%d/%m/%Y %H:%M"),
        "pergunta": pergunta,
        "idioma": idioma,
        "similaridade_minima": servico_pergunta.SIMILARIDADE_MINIMA,
        "top_k": servico_pergunta.TOP_K,
        "inventario": sorted(inv.values(), key=lambda d: d["codigo"]),
        "usuarios": resultados,
    }
    dados["criterios"] = avaliar(dados)
    return dados


# -- critérios ----------------------------------------------------------------
def _ids_do_sistema(usuario):
    return {f["documento_id"] for f in usuario["fontes"] if f["documento_id"]}


def _mencoes_a_bloqueados(usuario):
    """Código ou título de documento bloqueado escrito no texto da resposta.
    É só aviso: um documento permitido pode citar outro (referências cruzadas)."""
    texto = usuario["resposta"].lower()
    return sorted(
        {
            b["codigo"]
            for b in usuario["bloqueados"]
            if b["codigo"].lower() in texto or b["titulo"].lower() in texto
        }
    )


def avaliar(dados):
    a, b = dados["usuarios"][0], dados["usuarios"][1]
    ids_a, ids_b = _ids_do_sistema(a), _ids_do_sistema(b)

    mesma = bool(dados["pergunta"].strip())
    c1 = {
        "id": 1,
        "criterio": "Mesma pergunta feita com os usuários de Engenharia e de Qualidade",
        "ok": mesma,
        "detalhe": f"“{dados['pergunta']}” ({dados['idioma']}) para {a['nome']} e "
        f"{b['nome']}.",
    }

    if not ids_a and not ids_b:
        ok2, det2 = (
            None,
            (
                "Nenhum documento do sistema entrou nas fontes de nenhum dos dois: "
                "não dá para demonstrar. Suba documentos reais em áreas diferentes, "
                "aprove, espere o processamento e repita."
            ),
        )
    elif ids_a == ids_b:
        ok2, det2 = (
            False,
            (
                "Os dois receberam os mesmos documentos do sistema. Confira se há "
                "documento em área/nível que só um dos perfis acessa e se a pergunta "
                "o alcança."
            ),
        )
    else:
        so_a, so_b = len(ids_a - ids_b), len(ids_b - ids_a)
        ok2, det2 = (
            True,
            (
                f"{so_a} documento(s) só para {a['nome']}, {so_b} só para "
                f"{b['nome']}, {len(ids_a & ids_b)} em comum."
            ),
        )
    c2 = {
        "id": 2,
        "criterio": "Fontes diferentes conforme a permissão de cada um",
        "ok": ok2,
        "detalhe": det2,
    }

    violacoes = []
    for u in (a, b):
        bloqueados = {x["trecho_id"] for x in u["bloqueados"]}
        for f in u["fontes"]:
            if f["permitida"] is False or f["trecho_id"] in bloqueados:
                violacoes.append(f"{u['nome']}: {f['codigo']} p. {f['pagina']}")
    avisos = [
        f"{u['nome']} → {', '.join(_mencoes_a_bloqueados(u))}"
        for u in (a, b)
        if _mencoes_a_bloqueados(u)
    ]
    detalhe3 = (
        "Nenhuma fonte de nenhum dos dois fica fora da regra de permissão "
        "(conferida por fora da query) nem coincide com um trecho bloqueado."
    )
    if violacoes:
        detalhe3 = "FONTE FORA DA PERMISSÃO: " + "; ".join(violacoes)
    if avisos:
        detalhe3 += (
            " Atenção (não reprova): o texto da resposta menciona documento "
            "bloqueado, normalmente por referência cruzada dentro de um "
            "documento permitido: " + "; ".join(avisos)
        )
    c3 = {
        "id": 3,
        "criterio": "Nenhum trecho fora da permissão aparece na resposta",
        "ok": not violacoes,
        "detalhe": detalhe3,
    }
    return [c1, c2, c3]


def reprovado(dados):
    return any(c["ok"] is False for c in dados["criterios"])


def inconclusivo(dados):
    return any(c["ok"] is None for c in dados["criterios"])


# -- relatório ------------------------------------------------------------------
def _celula(texto):
    return " ".join(str(texto if texto is not None else "").split()).replace("|", "\\|")


def selo(ok):
    return {True: "PASSOU", False: "REPROVOU", None: "INCONCLUSIVO"}[ok]


def _tabela(cabecalho, linhas):
    saida = ["| " + " | ".join(cabecalho) + " |"]
    saida.append("|" + "|".join(" --- " for _ in cabecalho) + "|")
    saida += ["| " + " | ".join(_celula(c) for c in linha) + " |" for linha in linhas]
    return saida


def renderizar_markdown(dados):
    a, b = dados["usuarios"]
    out = [
        "# Validação do filtro de permissão: mesma pergunta, dois perfis",
        "",
        f"- **Gerado em:** {dados['gerado_em']} (horário de Brasília)",
        f"- **Pergunta:** {dados['pergunta']}",
        f"- **Idioma:** {dados['idioma']}",
        f"- **Modelos:** embedding `{a['modelos']['embedding']}`, "
        f"LLM `{a['modelos']['llm'] or b['modelos']['llm']}`",
        f"- **Parâmetros:** similaridade mínima {dados['similaridade_minima']}, "
        f"até {dados['top_k']} trechos por resposta",
        "",
        "## Resultado",
        "",
    ]
    out += _tabela(
        ["#", "Critério", "Resultado", "Detalhe"],
        [
            [c["id"], c["criterio"], selo(c["ok"]), c["detalhe"]]
            for c in dados["criterios"]
        ],
    )
    out += [
        "",
        "## Permissões dos usuários demo",
        "",
        "Regra: usuário + área + nível de sigilo máximo; sem herança por setor "
        "nem por papel.",
        "",
    ]
    linhas = []
    for u in (a, b):
        for p in u["permissoes"] or [{"area": "(nenhuma)", "nivel": "-"}]:
            linhas.append([u["nome"], u["email"], p["area"], p["nivel"]])
    out += _tabela(["Usuário", "E-mail", "Área", "Nível máximo"], linhas)

    out += ["", "## Acervo disponível e quem enxerga cada documento", ""]
    linhas = []
    for d in dados["inventario"]:
        ver = [d["codigo"] in u["visiveis"] for u in (a, b)]
        linhas.append(
            [
                d["codigo"],
                d["titulo"],
                ", ".join(x["nome"] for x in d["areas"]),
                d["sigilo"],
                d["trechos"],
                "sim" if ver[0] else "não",
                "sim" if ver[1] else "não",
            ]
        )
    out += _tabela(
        ["Documento", "Título", "Áreas", "Sigilo", "Trechos", a["nome"], b["nome"]],
        linhas,
    )

    out += ["", "## Fontes devolvidas a cada perfil", ""]
    for u in (a, b):
        out += [f"### {u['nome']} ({u['email']})", ""]
        if not u["fontes"]:
            out += ["Nenhuma fonte (a IA respondeu que não encontrou).", ""]
            continue
        out += _tabela(
            [
                "#",
                "Origem",
                "Documento",
                "Título",
                "Áreas",
                "Sigilo",
                "Pág.",
                "Similar.",
                "Citada",
                "Dentro da regra",
            ],
            [
                [
                    f["numero"],
                    f["origem"],
                    f["codigo"],
                    f["titulo"],
                    ", ".join(f["areas"]) or "-",
                    f["sigilo"],
                    f["pagina"],
                    f["similaridade"],
                    "sim" if f["citada"] else "não",
                    {True: "sim", False: "NÃO", None: "n/a (pública)"}[f["permitida"]],
                ]
                for f in u["fontes"]
            ],
        )
        out.append("")

    out += [
        "## O que o filtro bloqueou",
        "",
        "Busca de controle, SEM filtro: trechos de documentos do sistema que "
        "passam da similaridade mínima e que o perfil não pode acessar. Eles "
        "seriam usados se não houvesse permissão, e não aparecem nas fontes.",
        "",
    ]
    for u in (a, b):
        out += [f"### Bloqueados para {u['nome']}", ""]
        if not u["bloqueados"]:
            out += ["Nenhum trecho relevante foi bloqueado.", ""]
            continue
        out += _tabela(
            ["Documento", "Título", "Áreas", "Sigilo", "Pág.", "Similar.", "Motivo"],
            [
                [
                    x["codigo"],
                    x["titulo"],
                    ", ".join(x["areas"]),
                    x["sigilo"],
                    x["pagina"],
                    x["similaridade"],
                    x["motivo"],
                ]
                for x in u["bloqueados"]
            ],
        )
        out.append("")

    out += ["## Respostas da IA", ""]
    for u in (a, b):
        out += [f"### {u['nome']}", ""]
        out += [f"> {linha}" if linha else ">" for linha in u["resposta"].splitlines()]
        out.append("")

    out += [
        "## Como reproduzir",
        "",
        "```",
        "python manage.py seed_taxonomia",
        "python manage.py seed_usuarios_demo",
        f'python manage.py validar_permissao_demo --pergunta "{dados["pergunta"]}"',
        "```",
        "",
        "Para os prints da Sprint Review: entrar na tela da IA como "
        "`engenharia@akaer.com.br` e como `qualidade@akaer.com.br`, fazer a "
        "pergunta acima e capturar a resposta com a lista de fontes de cada um.",
        "",
    ]
    return "\n".join(out)


def gravar(dados, pasta):
    """Grava o relatório em Markdown e devolve o caminho do arquivo."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(FUSO).strftime("%Y-%m-%d-%H%M")
    caminho = pasta / f"validacao-permissao-{carimbo}.md"
    caminho.write_text(renderizar_markdown(dados), encoding="utf-8")
    return caminho


def ascii_seguro(texto):
    """Para o console do Windows: sem acento, sem caractere fora do ASCII."""
    return (
        unicodedata.normalize("NFKD", str(texto))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
