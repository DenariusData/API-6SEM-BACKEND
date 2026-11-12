"""
Exportação dos dados pessoais do próprio usuário (AKA-75, US-27, LGPD).

    exportar(usuario_id, formato)     GET /api/auth/me/exportar/?formato=json|pdf

Base legal: LGPD (Lei 13.709/2018), art. 18, II (acesso) e V (portabilidade),
e art. 19: "declaração clara e completa" (o PDF, para a pessoa ler) e cópia
"em formato que permita a sua utilização subsequente" (o JSON, para outro
sistema importar).

Regras:
  - o id vem SEMPRE do token (quem chama passa `usuario_id`): ninguém exporta
    os dados de outra pessoa;
  - `coletar` busca tudo UMA vez e os dois formatos são gerados a partir do
    mesmo dicionário, então JSON e PDF têm exatamente o mesmo conteúdo;
  - os dois bancos são lidos separadamente (primeiro `credenciais_db`, depois
    o de negócio) e compostos aqui, nunca por JOIN (ver credenciais/db_router);
  - controles de segurança (hash da senha, token de confirmação, tentativas de
    login, bloqueio) NÃO são exportados. A lista do que ficou de fora, e por
    quê, vai no próprio arquivo (`nao_incluidos`);
  - no máximo 1 exportação por minuto por usuário (MuitosPedidos, HTTP 429);
  - cada exportação vai para o log de auditoria como DADOS_PESSOAIS_EXPORTADOS,
    só com o id do usuário e a data, nunca com os dados exportados.
"""

import html
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pymupdf
from django.db.models import Q
from django.utils import timezone

from core_api.models import (
    AcaoAuditoria,
    Documento,
    EntidadeAuditoria,
    HistoricoValidacao,
    LogAcesso,
    LogAuditoria,
    PerfilOperacional,
    RegistroPergunta,
)
from core_api.services.auditoria import registrar_acao
from credenciais.models import Usuario

BANCO_CREDENCIAIS = "credenciais_db"
FORMATOS = ("json", "pdf")
INTERVALO_EXPORTACAO = timedelta(minutes=1)
VERSAO_FORMATO = "1.0"
# O servidor guarda tudo em UTC. O JSON mantém o horário ISO com fuso; o PDF,
# que é para a pessoa ler, mostra no horário de Brasília.
FUSO_LEITURA = ZoneInfo("America/Sao_Paulo")

NAO_INCLUIDOS = [
    {
        "dado": "Senha",
        "motivo": "A senha nunca é guardada; existe só um hash (resumo "
        "criptográfico) dela, que não é dado legível e cuja divulgação "
        "facilitaria ataques à conta.",
    },
    {
        "dado": "Token de confirmação de e-mail e sua validade",
        "motivo": "É uma credencial temporária de segurança. Expor o valor "
        "permitiria confirmar a troca de e-mail sem acesso à caixa de entrada.",
    },
    {
        "dado": "Contagem de tentativas de login e bloqueio temporário",
        "motivo": "São controles contra tentativa de invasão. Os logins feitos "
        "aparecem no registro de atividades.",
    },
    {
        "dado": "Conteúdo dos arquivos dos documentos",
        "motivo": "Os documentos técnicos pertencem à empresa e não são dados "
        "pessoais. Constam apenas os dados de cadastro dos documentos ligados "
        "a você.",
    },
    {
        "dado": "Tempos de processamento das perguntas",
        "motivo": "São medições técnicas do sistema e não dizem nada sobre você.",
    },
    {
        "dado": "Resposta da IA e trechos de documentos usados em cada pergunta",
        "motivo": "São o texto gerado pela IA e o conteúdo dos documentos "
        "técnicos, que pertencem à empresa e não são dados pessoais. Suas "
        "perguntas e se tiveram resposta constam no arquivo.",
    },
]


class FormatoInvalido(Exception):
    """Formato de exportação fora de FORMATOS (HTTP 400)."""


class MuitosPedidos(Exception):
    """Mais de 1 exportação por minuto (HTTP 429)."""

    def __init__(self, mensagem, segundos):
        super().__init__(mensagem)
        self.segundos = segundos


@dataclass(frozen=True)
class Arquivo:
    conteudo: bytes
    content_type: str
    nome: str


# -- coleta -----------------------------------------------------------------
def _data(valor):
    return valor.isoformat() if valor else None


def coletar(usuario_id) -> dict:
    """
    Todos os dados pessoais do usuário `usuario_id`, num dicionário só com
    tipos de JSON. Levanta Usuario.DoesNotExist ou ValueError (id inválido).
    """
    usuario_uuid = UUID(str(usuario_id))

    # 1) Banco de credenciais: identidade.
    usuario = (
        Usuario.objects.using(BANCO_CREDENCIAIS)
        .select_related("papel")
        .get(pk=usuario_uuid)
    )

    # 2) Banco de negócio: tudo o que referencia o mesmo uuid por valor.
    perfil = PerfilOperacional.objects.filter(pk=usuario_uuid).first()

    perguntas = RegistroPergunta.objects.filter(usuario_id=usuario_uuid).order_by(
        "criado_em", "id"
    )

    documentos = (
        Documento.objects.filter(
            Q(usuario_upload_id=usuario_uuid)
            | Q(responsavel_id=usuario_uuid)
            | Q(validado_por_id=usuario_uuid)
        )
        .order_by("criado_em", "id")
        .only(
            "id",
            "identificador_codigo",
            "numero_revisao",
            "titulo",
            "criado_em",
            "usuario_upload_id",
            "responsavel_id",
            "validado_por_id",
            "validado_em",
        )
    )

    historico = (
        HistoricoValidacao.objects.filter(usuario_id=usuario_uuid)
        .select_related("documento")
        .order_by("criado_em", "id")
    )

    # Sem select_related: o LogAcesso sobrevive ao documento apagado
    # (AKA-80, sem FK no banco) e o JOIN esconderia esses acessos. Os
    # identificadores vêm à parte; documento que não existe mais fica vazio.
    acessos = list(
        LogAcesso.objects.filter(usuario_id=usuario_uuid).order_by("criado_em", "id")
    )
    identificadores = dict(
        Documento.objects.filter(pk__in={a.documento_id for a in acessos}).values_list(
            "id", "identificador_codigo"
        )
    )

    atividades = LogAuditoria.objects.filter(usuario_id=usuario_uuid).order_by(
        "criado_em", "id"
    )

    return {
        "exportacao": {
            "gerada_em": _data(timezone.now()),
            "versao_formato": VERSAO_FORMATO,
            "base_legal": "LGPD (Lei 13.709/2018), art. 18, II e V, e art. 19",
        },
        "identificacao": {
            "id": str(usuario.id),
            "nome": usuario.nome,
            "email": usuario.email,
            "email_pendente_confirmacao": usuario.email_pendente,
            "papel": {
                "codigo": usuario.papel_id,
                "descricao": usuario.papel.descricao,
            },
            "ativo": usuario.ativo,
            "criado_em": _data(usuario.criado_em),
            "ultimo_login_em": _data(usuario.ultimo_login_em),
        },
        "perfil_operacional": (
            {
                "matricula": perfil.matricula,
                "cargo": perfil.cargo,
                "avatar": perfil.avatar,
                "menus_liberados": list(perfil.allowed_menus or []),
            }
            if perfil
            else None
        ),
        "perguntas": [
            {
                "pergunta": p.pergunta,
                "idioma": p.idioma,
                "encontrou_resposta": p.encontrou,
                "quantidade_fontes": p.quantidade_fontes,
                "feita_em": _data(p.criado_em),
            }
            for p in perguntas
        ],
        "documentos": [_documento(d, usuario_uuid) for d in documentos],
        "historico_validacao": [
            {
                "documento_id": str(h.documento_id),
                "documento_identificador": h.documento.identificador_codigo,
                "acao": h.acao,
                "comentario": h.comentario,
                "data": _data(h.criado_em),
            }
            for h in historico
        ],
        "acessos_a_documentos": [
            {
                "documento_id": str(a.documento_id),
                "documento_identificador": identificadores.get(a.documento_id),
                "acao": a.acao,
                "data": _data(a.criado_em),
            }
            for a in acessos
        ],
        "registro_de_atividades": [
            {
                "acao": a.acao,
                "entidade": a.entidade,
                "registro_id": a.registro_id,
                "data": _data(a.criado_em),
            }
            for a in atividades
        ],
        "nao_incluidos": [dict(item) for item in NAO_INCLUIDOS],
    }


def _documento(doc, usuario_uuid) -> dict:
    vinculos = []
    if doc.usuario_upload_id == usuario_uuid:
        vinculos.append("ENVIOU")
    if doc.responsavel_id == usuario_uuid:
        vinculos.append("RESPONSAVEL")
    if doc.validado_por_id == usuario_uuid:
        vinculos.append("VALIDOU")
    return {
        "id": str(doc.id),
        "identificador": doc.identificador_codigo,
        "revisao": doc.numero_revisao,
        "titulo": doc.titulo,
        "criado_em": _data(doc.criado_em),
        "validado_em": _data(doc.validado_em)
        if doc.validado_por_id == usuario_uuid
        else None,
        "vinculo": vinculos,
    }


# -- formatos ---------------------------------------------------------------
def para_json(dados) -> bytes:
    return json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")


def _texto(valor) -> str:
    """Valor pronto para o HTML do PDF: escapado, com datas no horário de
    Brasília e booleanos/vazios em português."""
    if valor is None or valor == "" or valor == []:
        return "—"
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if isinstance(valor, list):
        return html.escape(", ".join(str(v) for v in valor))
    if isinstance(valor, str) and len(valor) >= 19 and valor[10:11] == "T":
        try:
            momento = datetime.fromisoformat(valor)
        except ValueError:
            return html.escape(valor)
        if momento.tzinfo is not None:
            return momento.astimezone(FUSO_LEITURA).strftime("%d/%m/%Y %H:%M")
    return html.escape(str(valor))


def _campos(pares) -> str:
    # Rótulo e valor em parágrafos, não em tabela: o layout de tabela do
    # MuPDF sem linha de cabeçalho não respeita a largura das colunas.
    return "".join(
        f"<p class='campo'><b>{rotulo}:</b> {_texto(v)}</p>" for rotulo, v in pares
    )


def _tabela(colunas, itens, vazio) -> str:
    if not itens:
        return f"<p class='vazio'>{vazio}</p>"
    cabecalho = "".join(f"<th>{rotulo}</th>" for rotulo, _ in colunas)
    linhas = "".join(
        "<tr>"
        + "".join(f"<td>{_texto(item.get(chave))}</td>" for _, chave in colunas)
        + "</tr>"
        for item in itens
    )
    return f"<table class='lista'><tr>{cabecalho}</tr>{linhas}</table>"


CSS_PDF = """
* { font-family: sans-serif; }
body { font-size: 9pt; color: #1f2933; }
h1 { font-size: 16pt; margin: 0 0 4pt 0; }
h2 { font-size: 11pt; margin: 14pt 0 4pt 0; color: #0b4f8a; }
p { margin: 0 0 4pt 0; }
.sub { color: #52606d; }
.vazio { color: #7b8794; font-style: italic; }
table { width: 100%; border-collapse: collapse; }
th, td { border: 0.5pt solid #cbd2d9; padding: 2pt 4pt; text-align: left;
         vertical-align: top; }
th { background-color: #f0f4f8; }
.campo { margin: 0 0 2pt 0; }
"""


def _html(dados) -> str:
    ident = dados["identificacao"]
    perfil = dados["perfil_operacional"] or {}
    partes = [
        "<h1>Meus dados pessoais</h1>",
        f"<p class='sub'>Gerado em {_texto(dados['exportacao']['gerada_em'])} "
        "(horário de Brasília) · "
        f"{html.escape(dados['exportacao']['base_legal'])}</p>",
        "<p>Este documento reúne os dados pessoais que o AkaVision guarda sobre "
        "você. A mesma informação está disponível em JSON, para importação em "
        "outro sistema.</p>",
        "<h2>1. Identificação</h2>",
        _campos(
            [
                ("Nome", ident["nome"]),
                ("E-mail", ident["email"]),
                ("E-mail aguardando confirmação", ident["email_pendente_confirmacao"]),
                ("Papel", ident["papel"]["descricao"] or ident["papel"]["codigo"]),
                ("Conta ativa", ident["ativo"]),
                ("Conta criada em", ident["criado_em"]),
                ("Último login em", ident["ultimo_login_em"]),
                ("Identificador interno", ident["id"]),
            ]
        ),
        "<h2>2. Perfil operacional</h2>",
        _campos(
            [
                ("Matrícula", perfil.get("matricula")),
                ("Cargo", perfil.get("cargo")),
                ("Avatar", perfil.get("avatar")),
                ("Menus liberados", perfil.get("menus_liberados")),
            ]
        )
        if perfil
        else "<p class='vazio'>Sem perfil operacional cadastrado.</p>",
        "<h2>3. Perguntas feitas ao assistente</h2>",
        _tabela(
            [
                ("Data", "feita_em"),
                ("Pergunta", "pergunta"),
                ("Idioma", "idioma"),
                ("Encontrou resposta", "encontrou_resposta"),
            ],
            dados["perguntas"],
            "Nenhuma pergunta registrada.",
        ),
        "<h2>4. Documentos ligados a você</h2>",
        _tabela(
            [
                ("Identificador", "identificador"),
                ("Revisão", "revisao"),
                ("Título", "titulo"),
                ("Vínculo", "vinculo"),
                ("Criado em", "criado_em"),
            ],
            dados["documentos"],
            "Nenhum documento enviado, sob sua responsabilidade ou validado por você.",
        ),
        "<h2>5. Histórico de validação</h2>",
        _tabela(
            [
                ("Data", "data"),
                ("Documento", "documento_identificador"),
                ("Ação", "acao"),
                ("Comentário", "comentario"),
            ],
            dados["historico_validacao"],
            "Nenhuma ação de validação registrada.",
        ),
        "<h2>6. Acessos a documentos</h2>",
        _tabela(
            [
                ("Data", "data"),
                ("Documento", "documento_identificador"),
                ("Ação", "acao"),
            ],
            dados["acessos_a_documentos"],
            "Nenhum acesso registrado.",
        ),
        "<h2>7. Registro de atividades</h2>",
        _tabela(
            [
                ("Data", "data"),
                ("Ação", "acao"),
                ("Sobre", "entidade"),
                ("Registro", "registro_id"),
            ],
            dados["registro_de_atividades"],
            "Nenhuma atividade registrada.",
        ),
        "<h2>8. O que não foi incluído e por quê</h2>",
        _tabela(
            [("Dado", "dado"), ("Motivo", "motivo")],
            dados["nao_incluidos"],
            "—",
        ),
    ]
    return "<body>" + "".join(partes) + "</body>"


def para_pdf(dados) -> bytes:
    pagina = pymupdf.paper_rect("a4")
    area = pagina + (40, 40, -40, -50)
    story = pymupdf.Story(html=_html(dados), user_css=CSS_PDF)

    saida = io.BytesIO()
    writer = pymupdf.DocumentWriter(saida)
    mais = True
    while mais:
        dispositivo = writer.begin_page(pagina)
        mais, _ = story.place(area)
        story.draw(dispositivo)
        writer.end_page()
    writer.close()

    # Segunda passada só para o rodapé "Página X de Y".
    doc = pymupdf.open("pdf", saida.getvalue())
    total = doc.page_count
    for numero, pag in enumerate(doc, start=1):
        pag.insert_text(
            (40, pagina.height - 25),
            f"AkaVision · Meus dados pessoais · Página {numero} de {total}",
            fontsize=7,
            color=(0.45, 0.5, 0.55),
        )
    doc.set_metadata({"title": "Meus dados pessoais", "creator": "AkaVision"})
    conteudo = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return conteudo


# -- operação ---------------------------------------------------------------
def _exigir_intervalo(usuario_uuid) -> None:
    ultima = (
        LogAuditoria.objects.filter(
            usuario_id=usuario_uuid,
            acao=AcaoAuditoria.DADOS_PESSOAIS_EXPORTADOS,
            criado_em__gt=timezone.now() - INTERVALO_EXPORTACAO,
        )
        .order_by("-criado_em")
        .values_list("criado_em", flat=True)
        .first()
    )
    if ultima is not None:
        faltam = INTERVALO_EXPORTACAO - (timezone.now() - ultima)
        segundos = max(int(faltam.total_seconds()) + 1, 1)
        raise MuitosPedidos(
            f"Aguarde {segundos} segundos para exportar novamente.", segundos
        )


def exportar(usuario_id, formato) -> Arquivo:
    """
    Gera o arquivo de exportação do usuário `usuario_id` (o `sub` do JWT).
    Levanta FormatoInvalido, MuitosPedidos, Usuario.DoesNotExist ou
    ValueError (id inválido).
    """
    formato = formato.strip().lower() if isinstance(formato, str) else ""
    if formato not in FORMATOS:
        raise FormatoInvalido("Formato inválido. Use 'json' ou 'pdf'.")

    usuario_uuid = UUID(str(usuario_id))
    _exigir_intervalo(usuario_uuid)
    dados = coletar(usuario_uuid)

    if formato == "json":
        conteudo, content_type = para_json(dados), "application/json; charset=utf-8"
    else:
        conteudo, content_type = para_pdf(dados), "application/pdf"

    registrar_acao(
        usuario_uuid,
        AcaoAuditoria.DADOS_PESSOAIS_EXPORTADOS,
        EntidadeAuditoria.USUARIO,
        usuario_uuid,
    )
    hoje = timezone.localdate(timezone=FUSO_LEITURA).isoformat()
    return Arquivo(conteudo, content_type, f"meus-dados-{hoje}.{formato}")
