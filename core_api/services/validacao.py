"""
Regras do fluxo de validação do documento (S2-34): aprovar e devolver.

    AGUARDANDO_VALIDACAO --devolver--> DEVOLVIDO
    AGUARDANDO_VALIDACAO --aprovar---> DISPONIVEL

Todas as regras são verificadas AQUI, no backend, e não no front:
  - só ADMINISTRADOR aprova ou devolve;
  - o autor do documento (quem fez o upload) não valida o próprio documento,
    nem sendo administrador: outro administrador precisa validar;
  - o documento precisa estar AGUARDANDO_VALIDACAO;
  - devolver exige comentário (mínimo de COMENTARIO_MINIMO caracteres);
  - nível final CONFIDENCIAL exige confirmação explícita
    (`confirmar_confidencial=True`). Nunca há aprovação automática ou em lote:
    cada chamada trata exatamente um documento.

Estado + histórico são gravados na MESMA transação, com o documento travado
(`select_for_update`): duas ações simultâneas no mesmo documento não passam
pela checagem de estado ao mesmo tempo, e não existe mudança de estado sem
linha em `HistoricoValidacao`.

O papel NÃO é lido do banco aqui: `Usuario`/`Papel` vivem em `credenciais_db`.
Quem chama passa o `papel_codigo` que já veio validado no JWT.

Depois do commit da aprovação, o documento é processado para a IA em
segundo plano (services/processamento.py, AKA-62): a aprovação responde na
hora, sem esperar.
"""

from django.db import transaction
from django.utils import timezone

from core_api.models import (
    AcaoValidacao,
    Documento,
    HistoricoValidacao,
    NivelSigilo,
    StatusValidacao,
)
from core_api.services.processamento import agendar_processamento
from credenciais.models import Papel

COMENTARIO_MINIMO = 10
# Mesmo valor que o trigger trg_documento_confidencial_validado usa (0005).
NOME_NIVEL_CONFIDENCIAL = "CONFIDENCIAL"


class PermissaoNegada(Exception):
    """O usuário não pode executar esta ação (HTTP 403)."""


class TransicaoInvalida(Exception):
    """A ação não se aplica ao estado atual do documento (HTTP 409)."""


class DadosInvalidos(Exception):
    """Dados da requisição inválidos (HTTP 400). `campo` diz qual."""

    def __init__(self, mensagem, campo=None):
        super().__init__(mensagem)
        self.campo = campo


def _travar(documento_id) -> Documento:
    """Relê o documento com lock de linha. Só vale dentro de transaction.atomic().
    Levanta Documento.DoesNotExist se o id não existe."""
    return (
        Documento.objects.select_for_update()
        .select_related("nivel_sigilo")
        .get(pk=documento_id)
    )


def _exigir_validador(doc, usuario, papel_codigo, verbo) -> None:
    if papel_codigo != Papel.ADMINISTRADOR:
        raise PermissaoNegada(
            "Apenas usuários com papel ADMINISTRADOR podem aprovar ou devolver "
            "documentos."
        )
    if doc.usuario_upload_id == usuario.pk:
        raise PermissaoNegada(f"Você não pode {verbo} um documento enviado por você")


def _exigir_aguardando(doc, verbo) -> None:
    if doc.status_validacao != StatusValidacao.AGUARDANDO_VALIDACAO:
        raise TransicaoInvalida(
            f"Só um documento aguardando validação pode ser {verbo}; "
            f"este está {doc.get_status_validacao_display().lower()}."
        )


def _resolver_nivel(nome) -> NivelSigilo:
    if not isinstance(nome, str) or not nome.strip():
        raise DadosInvalidos("Informe o nível de sigilo pelo nome.", "nivel_sigilo")
    nivel = NivelSigilo.objects.filter(nome__iexact=nome.strip()).first()
    if nivel is None:
        raise DadosInvalidos("Nível de sigilo inexistente.", "nivel_sigilo")
    return nivel


@transaction.atomic
def aprovar(
    documento_id,
    usuario,
    papel_codigo,
    nivel_sigilo=None,
    confirmar_confidencial=False,
) -> Documento:
    """
    Aprova o documento: fica DISPONIVEL com o nível FINAL decidido
    (`nivel_sigilo`, nome do nível; se omitido, vale o nível atual do
    documento) e com `validado_por`/`validado_em` preenchidos. O
    `nivel_sigilo_sugerido` do upload continua guardado.

    `usuario` é o PerfilOperacional de quem valida.
    """
    doc = _travar(documento_id)
    _exigir_validador(doc, usuario, papel_codigo, "aprovar")
    _exigir_aguardando(doc, "aprovado")

    final = doc.nivel_sigilo if nivel_sigilo is None else _resolver_nivel(nivel_sigilo)
    if final.nome == NOME_NIVEL_CONFIDENCIAL and confirmar_confidencial is not True:
        raise DadosInvalidos(
            "Aprovar como CONFIDENCIAL exige confirmação explícita "
            '(envie "confirmar_confidencial": true).',
            "confirmar_confidencial",
        )

    doc.status_validacao = StatusValidacao.DISPONIVEL
    doc.nivel_sigilo = final
    doc.validado_por = usuario
    doc.validado_em = timezone.now()
    doc.save(
        update_fields=[
            "status_validacao",
            "nivel_sigilo",
            "validado_por",
            "validado_em",
            "atualizado_em",
        ]
    )
    HistoricoValidacao.objects.create(
        documento=doc,
        acao=AcaoValidacao.APROVADO,
        usuario=usuario,
        nivel_sigilo=final,
    )
    # Só começa depois do commit: se a aprovação for desfeita, nada roda.
    agendar_processamento(doc.pk)
    return doc


@transaction.atomic
def devolver(documento_id, usuario, papel_codigo, comentario) -> Documento:
    """Devolve o documento ao responsável para correção. O motivo é obrigatório."""
    doc = _travar(documento_id)
    _exigir_validador(doc, usuario, papel_codigo, "devolver")
    _exigir_aguardando(doc, "devolvido")

    comentario = comentario.strip() if isinstance(comentario, str) else ""
    if len(comentario) < COMENTARIO_MINIMO:
        raise DadosInvalidos(
            f"Informe o motivo da devolução (mínimo de {COMENTARIO_MINIMO} "
            "caracteres).",
            "comentario",
        )

    doc.status_validacao = StatusValidacao.DEVOLVIDO
    doc.comentario_devolucao = comentario
    doc.save(
        update_fields=["status_validacao", "comentario_devolucao", "atualizado_em"]
    )
    HistoricoValidacao.objects.create(
        documento=doc,
        acao=AcaoValidacao.DEVOLVIDO,
        usuario=usuario,
        comentario=comentario,
    )
    return doc
