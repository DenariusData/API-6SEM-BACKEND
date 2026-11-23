"""
Consulta do log de auditoria (AKA-81, RF29, épico E5 — Privacidade e LGPD).

Junta as duas trilhas do sistema numa lista só, do mais recente para o mais
antigo:
  - log_auditoria: criação, alteração, exclusão, fluxo de validação, login,
    dados pessoais (AKA-31);
  - log_acesso: visualização de documento (AKA-61), com entidade DOCUMENTO e
    o id do documento como registro.

Filtros (todos opcionais e combináveis):
  - usuario: uuid do usuário;
  - de / ate: datas AAAA-MM-DD, inclusivas, no horário de Brasília;
  - acao: código de uma ação de qualquer das duas trilhas.

O log guarda só o uuid (AKA-80). O nome é buscado em credenciais_db na hora
de exibir, numa consulta por página (ou por lote, na exportação). Usuário
excluído aparece como "Usuário removido". Linha sem usuário aparece como
"Não identificado" quando é login com e-mail inexistente (alguém tentou
entrar) e como "Sistema" nas demais, que são ações automáticas (como o
processamento do documento para a IA depois da aprovação).

Só leitura: nada aqui altera o log, que de qualquer forma é só de inclusão.
"""

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import CharField, F, Value
from django.db.models.functions import Cast

from core_api.models import (
    AcaoAcesso,
    AcaoAuditoria,
    EntidadeAuditoria,
    LogAcesso,
    LogAuditoria,
)
from core_api.services.consulta_documentos import (
    ParametroInvalido,
    nomes_dos_usuarios,
    paginar,
)

FUSO_LEITURA = ZoneInfo("America/Sao_Paulo")

USUARIO_REMOVIDO = "Usuário removido"
SEM_USUARIO = "Não identificado"
SISTEMA = "Sistema"

# Rótulos das duas trilhas. Os códigos não se repetem entre elas.
ROTULOS_ACAO = {**dict(AcaoAuditoria.choices), **dict(AcaoAcesso.choices)}
ROTULOS_ENTIDADE = dict(EntidadeAuditoria.choices)

ORIGEM_AUDITORIA = "auditoria"
ORIGEM_ACESSO = "acesso"

# Tamanho do lote da exportação: os nomes são buscados uma vez por lote.
LOTE_EXPORTACAO = 500

# Colunas do UNION. Todas são anotações, definidas na mesma ordem nos dois
# lados: o Django põe as colunas do model antes das anotações no SELECT, e
# as duas tabelas têm campos diferentes, então só assim elas se alinham.
COLUNAS = ("origem", "id", "usuario_id", "acao", "entidade", "registro_id", "criado_em")


# -- filtros ------------------------------------------------------------
def _data(valor, campo):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ParametroInvalido("Data inválida. Use o formato AAAA-MM-DD.", campo)


def ler_filtros(params) -> dict:
    """Valida os parâmetros da query string. Vazio = sem filtro."""
    filtros = {}

    usuario = (params.get("usuario") or "").strip()
    if usuario:
        try:
            filtros["usuario"] = uuid.UUID(usuario)
        except ValueError:
            raise ParametroInvalido("Usuário inválido.", "usuario")

    de = (params.get("de") or "").strip()
    ate = (params.get("ate") or "").strip()
    if de:
        filtros["de"] = _data(de, "de")
    if ate:
        filtros["ate"] = _data(ate, "ate")
    if de and ate and filtros["ate"] < filtros["de"]:
        raise ParametroInvalido("A data final não pode ser anterior à inicial.", "ate")

    acao = (params.get("acao") or "").strip().upper()
    if acao:
        if acao not in ROTULOS_ACAO:
            raise ParametroInvalido("Ação inválida.", "acao")
        filtros["acao"] = acao

    return filtros


def _inicio_do_dia(dia):
    return datetime.combine(dia, time.min, tzinfo=FUSO_LEITURA)


def _filtrar(queryset, filtros, campo_usuario):
    if "usuario" in filtros:
        queryset = queryset.filter(**{campo_usuario: filtros["usuario"]})
    if "de" in filtros:
        queryset = queryset.filter(criado_em__gte=_inicio_do_dia(filtros["de"]))
    if "ate" in filtros:
        # Até o fim do dia: tudo antes da 0h do dia seguinte, em Brasília.
        fim = _inicio_do_dia(filtros["ate"] + timedelta(days=1))
        queryset = queryset.filter(criado_em__lt=fim)
    if "acao" in filtros:
        queryset = queryset.filter(acao=filtros["acao"])
    return queryset


# -- consulta -----------------------------------------------------------
def consultar(filtros):
    """Queryset único (UNION) das duas trilhas, já filtrado e ordenado do
    mais recente para o mais antigo. Cada linha é um dict com COLUNAS."""
    acao = filtros.get("acao")
    auditoria = acessos = None

    if acao is None or acao in AcaoAuditoria.values:
        auditoria = _colunas(
            _filtrar(LogAuditoria.objects.all(), filtros, "usuario_id"),
            origem=Value(ORIGEM_AUDITORIA, CharField()),
            entidade=F("entidade"),
            registro_id=F("registro_id"),
        )

    if acao is None or acao in AcaoAcesso.values:
        acessos = _colunas(
            _filtrar(LogAcesso.objects.all(), filtros, "usuario_id"),
            origem=Value(ORIGEM_ACESSO, CharField()),
            entidade=Value(EntidadeAuditoria.DOCUMENTO, CharField()),
            registro_id=Cast("documento_id", CharField()),
        )

    # ler_filtros só aceita ação de uma das duas trilhas; o erro abaixo só
    # protege quem chamar consultar() sem passar por ele.
    if auditoria is None and acessos is None:
        raise ParametroInvalido("Ação inválida.", "acao")
    if auditoria is None:
        consulta = acessos
    elif acessos is None:
        consulta = auditoria
    else:
        consulta = auditoria.union(acessos, all=True)
    return consulta.order_by("-l_criado_em", "-l_id")


def _colunas(queryset, origem, entidade, registro_id):
    """Mesmas anotações, na mesma ordem, para as duas trilhas (ver COLUNAS).
    O prefixo l_ evita conflito com os nomes dos campos dos models."""
    return (
        queryset.order_by()
        .annotate(
            l_origem=origem,
            l_id=F("id"),
            l_usuario_id=F("usuario_id"),
            l_acao=F("acao"),
            l_entidade=entidade,
            l_registro_id=registro_id,
            l_criado_em=F("criado_em"),
        )
        .values(*(f"l_{c}" for c in COLUNAS))
    )


def _sem_prefixo(linha):
    return {c: linha[f"l_{c}"] for c in COLUNAS}


def _nome(usuario_id, nomes, acao=None):
    if usuario_id is None:
        return SEM_USUARIO if acao == AcaoAuditoria.LOGIN_FALHOU else SISTEMA
    return nomes.get(usuario_id, USUARIO_REMOVIDO)


def serializar(linhas):
    """Linhas da página prontas para o JSON, com os nomes buscados uma vez."""
    linhas = [_sem_prefixo(linha) for linha in linhas]
    nomes = nomes_dos_usuarios(linha["usuario_id"] for linha in linhas)
    return [
        {
            "id": f"{linha['origem']}-{linha['id']}",
            "criado_em": linha["criado_em"].isoformat(),
            "usuario_id": str(linha["usuario_id"]) if linha["usuario_id"] else None,
            "usuario_nome": _nome(linha["usuario_id"], nomes, linha["acao"]),
            "acao": linha["acao"],
            "acao_rotulo": ROTULOS_ACAO.get(linha["acao"], linha["acao"]),
            "entidade": linha["entidade"],
            "entidade_rotulo": ROTULOS_ENTIDADE.get(
                linha["entidade"], linha["entidade"]
            ),
            "registro_id": linha["registro_id"],
        }
        for linha in linhas
    ]


def listar(params) -> dict:
    """Página do log com os filtros da query string, mais as ações
    disponíveis para o filtro da tela."""
    filtros = ler_filtros(params)
    corpo = paginar(consultar(filtros), params.get("page"), serializar)
    corpo["acoes"] = [
        {"valor": valor, "rotulo": rotulo} for valor, rotulo in ROTULOS_ACAO.items()
    ]
    return corpo


# -- exportação CSV ----------------------------------------------------
CABECALHO_CSV = (
    "Data/hora (Brasília)",
    "Usuário",
    "ID do usuário",
    "Ação",
    "Entidade",
    "Registro",
)


def _celula(valor) -> str:
    """Texto seguro para planilha: célula que começa com =, +, - ou @ vira
    fórmula no Excel (CSV injection), então ganha um apóstrofo na frente."""
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + texto
    return texto


def _linhas_csv(consulta):
    lote = []
    for linha in consulta.iterator(chunk_size=LOTE_EXPORTACAO):
        lote.append(linha)
        if len(lote) == LOTE_EXPORTACAO:
            yield from serializar(lote)
            lote = []
    if lote:
        yield from serializar(lote)


def exportar_csv(params) -> tuple[bytes, str]:
    """CSV do resultado filtrado inteiro (sem paginação), no formato que o
    Excel em português abre direto: UTF-8 com BOM e ponto e vírgula."""
    filtros = ler_filtros(params)
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(CABECALHO_CSV)
    for item in _linhas_csv(consultar(filtros)):
        momento = datetime.fromisoformat(item["criado_em"]).astimezone(FUSO_LEITURA)
        escritor.writerow(
            [
                momento.strftime("%d/%m/%Y %H:%M:%S"),
                _celula(item["usuario_nome"]),
                item["usuario_id"] or "",
                _celula(item["acao_rotulo"]),
                _celula(item["entidade_rotulo"]),
                _celula(item["registro_id"]),
            ]
        )
    hoje = datetime.now(FUSO_LEITURA).strftime("%Y-%m-%d")
    return ("﻿" + saida.getvalue()).encode("utf-8"), f"log-auditoria-{hoje}.csv"
