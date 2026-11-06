"""
Reenvio do documento devolvido (S2-36).

    DEVOLVIDO --reenviar--> AGUARDANDO_VALIDACAO

O autor (quem fez o upload) corrige o que o validador pediu e devolve o
documento para a fila. Aceita metadados, arquivo novo, ou os dois.

Regras:
  - só o autor reenvia (PermissaoNegada, 403). Nem administrador reenvia
    documento de outra pessoa;
  - só documento DEVOLVIDO (TransicaoInvalida, 409);
  - a validação é a MESMA do upload: `upload_documento.validar` em modo
    parcial, sem regra copiada. Só os campos enviados são validados e
    alterados; o resto fica como está. Requisição sem nenhum campo e sem
    arquivo é DadosInvalidos (não há o que reenviar);
  - arquivo novo substitui o antigo. O antigo só é apagado do disco DEPOIS do
    commit: se a transação falhar, o documento continua apontando para um
    arquivo que existe. Se a transação falhar, é o arquivo NOVO que é apagado;
  - o documento volta AGUARDANDO_VALIDACAO, `comentario_devolucao` é limpo e o
    histórico ganha a linha REENVIADO. O comentário da devolução continua na
    linha DEVOLVIDO do histórico, que é append-only;
  - estado, campos, áreas e histórico são gravados na MESMA transação, com o
    documento travado (`select_for_update`): dois reenvios simultâneos não
    passam juntos pela checagem de estado.

O papel não importa aqui: o critério é ser o autor.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction

from core_api.models import (
    AcaoValidacao,
    DocumentoArea,
    HistoricoValidacao,
    StatusValidacao,
)
from core_api.services import upload_documento
from core_api.services.upload_documento import DadosInvalidos, DocumentoDuplicado
from core_api.services.validacao import PermissaoNegada, TransicaoInvalida, _travar

logger = logging.getLogger(__name__)

# Campos que vão direto do formulário validado para o Documento.
CAMPOS_DIRETOS = (
    "titulo",
    "identificador_codigo",
    "tipo_documento",
    "numero_revisao",
    "data_emissao",
    "data_vigencia",
    "data_validade",
    "subcategoria",
    "status",
    "origem_fonte",
    "responsavel",
    "idioma",
    "referencia_normativa",
)


def _apagar_arquivo(nome) -> None:
    """Apaga da pasta privada o arquivo de nome `nome`. Só o nome conta: o que
    está no banco nunca sai da pasta de uploads."""
    if not nome:
        return
    caminho = Path(settings.DOCUMENTOS_UPLOAD_DIR) / Path(nome).name
    try:
        caminho.unlink(missing_ok=True)
    except OSError:
        # O reenvio já valeu; sobra um arquivo sem dono, não um erro para o autor.
        logger.warning("Não foi possível apagar o arquivo antigo %s", caminho)


def _aplicar(doc, campos) -> list:
    """Copia os campos validados para o documento e devolve os update_fields."""
    alterados = []
    for campo in CAMPOS_DIRETOS:
        if campo in campos:
            setattr(doc, campo, campos[campo])
            alterados.append(campo)
    if "nivel_sigilo" in campos:
        # Documento devolvido nunca foi validado: o nível final ainda é só o
        # sugerido pelo autor, e continuam iguais até alguém aprovar.
        doc.nivel_sigilo = campos["nivel_sigilo"]
        doc.nivel_sigilo_sugerido = campos["nivel_sigilo"]
        alterados += ["nivel_sigilo", "nivel_sigilo_sugerido"]
    return alterados


def _trocar_areas(doc, campos) -> None:
    DocumentoArea.objects.filter(documento=doc).delete()
    DocumentoArea.objects.create(
        documento=doc, area=campos["area_principal"], is_principal=True
    )
    DocumentoArea.objects.bulk_create(
        DocumentoArea(documento=doc, area=area, is_principal=False)
        for area in campos["areas_secundarias"]
    )


def reenviar(documento_id, dados, arquivo, usuario):
    """
    Reenvia o documento devolvido para a fila de validação. `usuario` é o
    PerfilOperacional de quem chamou (vem do JWT); `dados` é o QueryDict da
    requisição e `arquivo` o PDF novo, ou None.

    Levanta Documento.DoesNotExist (id inexistente), PermissaoNegada,
    TransicaoInvalida, DadosInvalidos, DocumentoDuplicado, OSError (falha ao
    gravar o arquivo novo) ou DatabaseError (já sem o arquivo novo na pasta).
    """
    novo = None
    antigo = None
    doc = None
    campos = {}
    try:
        # O commit acontece na saída do bloco, onde os triggers deferrable de
        # área principal conferem o documento; por isso o except cobre tudo.
        with transaction.atomic():
            doc = _travar(documento_id)
            if doc.usuario_upload_id != usuario.pk:
                raise PermissaoNegada("Só o autor do documento pode reenviá-lo.")
            if doc.status_validacao != StatusValidacao.DEVOLVIDO:
                raise TransicaoInvalida(
                    "Só um documento devolvido pode ser reenviado; "
                    f"este está {doc.get_status_validacao_display().lower()}."
                )

            if not dados.keys() and arquivo is None:
                raise DadosInvalidos(
                    "Envie ao menos um campo corrigido ou o arquivo novo.", None
                )
            campos = upload_documento.validar(
                dados,
                arquivo,
                atual={
                    "data_emissao": doc.data_emissao,
                    "data_vigencia": doc.data_vigencia,
                    "data_validade": doc.data_validade,
                },
            )
            identificador = campos.get("identificador_codigo", doc.identificador_codigo)
            revisao = campos.get("numero_revisao", doc.numero_revisao)
            if upload_documento._ja_existe(identificador, revisao, excluir_id=doc.pk):
                raise DocumentoDuplicado(
                    "Já existe um documento com este identificador e esta revisão."
                )

            alterados = _aplicar(doc, campos)
            if arquivo is not None:
                novo = upload_documento._gravar_arquivo(arquivo)
                antigo = doc.arquivo_original_url
                doc.arquivo_original_url = novo.name
                alterados.append("arquivo_original_url")
            if "area_principal" in campos:
                _trocar_areas(doc, campos)

            doc.status_validacao = StatusValidacao.AGUARDANDO_VALIDACAO
            doc.comentario_devolucao = None
            doc.save(
                update_fields=alterados
                + ["status_validacao", "comentario_devolucao", "atualizado_em"]
            )
            HistoricoValidacao.objects.create(
                documento=doc, acao=AcaoValidacao.REENVIADO, usuario=usuario
            )
    except BaseException as erro:
        if novo is not None:
            novo.unlink(missing_ok=True)
        # Dois reenvios com o mesmo identificador + revisão ao mesmo tempo: o
        # segundo esbarra na constraint única do banco.
        if (
            isinstance(erro, IntegrityError)
            and doc is not None
            and upload_documento._ja_existe(
                campos.get("identificador_codigo", doc.identificador_codigo),
                campos.get("numero_revisao", doc.numero_revisao),
                excluir_id=doc.pk,
            )
        ):
            raise DocumentoDuplicado(
                "Já existe um documento com este identificador e esta revisão."
            ) from erro
        raise
    if antigo and antigo != doc.arquivo_original_url:
        _apagar_arquivo(antigo)
    return doc
