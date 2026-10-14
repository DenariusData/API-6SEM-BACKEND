"""
Upload de documento (S2-32): valida os 16 campos, grava o PDF numa pasta
privada e cria o Documento aguardando validação.

Campos do formulário (multipart/form-data), na ordem da lista do cliente:

     1. titulo                   obrigatório
     2. identificador_codigo     obrigatório
     3. tipo_documento           obrigatório (id, GET /api/tipos-documento/)
     4. numero_revisao           obrigatório
     5. data_emissao             obrigatório (AAAA-MM-DD)
     6. data_vigencia            opcional, não pode ser anterior à emissão
     7. area_principal           obrigatório (id, GET /api/areas/)
        areas_secundarias        opcional, pode repetir o campo
     8. categoria, subcategoria  obrigatórios (ids, GET /api/areas/)
     9. status                   obrigatório (VIGENTE, SUBSTITUIDO, CANCELADO,
                                 EM_REVISAO)
    10. nivel_sigilo             obrigatório (id, GET /api/niveis-sigilo/)
    11. origem_fonte             obrigatório
    12. responsavel              obrigatório (id, GET /api/responsaveis/)
    13. idioma                   obrigatório (código, GET /api/idiomas/)
    14. referencia_normativa     opcional, texto livre nesta sprint
    15. data_validade            opcional, não pode ser anterior à emissão
    16. arquivo                  obrigatório, só PDF, até 10 MB

Regras:
  - o primeiro campo inválido levanta DadosInvalidos com o nome do campo;
  - identificador + revisão já cadastrados levantam DocumentoDuplicado;
  - quem fez o upload vem do token (quem chama passa o perfil), nunca do
    formulário;
  - o arquivo é gravado com nome gerado (uuid) em
    settings.DOCUMENTOS_UPLOAD_DIR, que não tem URL pública.
    `arquivo_original_url` guarda só esse nome;
  - Documento, DocumentoArea e a linha ENVIADO do histórico são gravados na
    MESMA transação. Se o banco falhar, o arquivo gravado é apagado: não
    sobra arquivo órfão na pasta;
  - o documento nasce AGUARDANDO_VALIDACAO, então não aparece na busca
    (`Documento.objects.disponiveis()`) até ser aprovado.
"""

import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core_api.models import (
    AcaoValidacao,
    Area,
    Categoria,
    Documento,
    DocumentoArea,
    HistoricoValidacao,
    Idioma,
    NivelSigilo,
    PerfilOperacional,
    StatusDocumentoChoices,
    StatusValidacao,
    Subcategoria,
    TipoDocumento,
)
from credenciais.models import Usuario

# Só PDF nesta sprint. DOC, DOCX, XLS, XLSX e TXT voltam quando o pipeline
# souber extrair texto deles.
ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
# Todo PDF começa com estes bytes. Barra arquivo renomeado para .pdf.
ASSINATURA_PDF = b"%PDF-"
TAMANHO_MAXIMO_REFERENCIA = 1000


class DadosInvalidos(Exception):
    """Campo do upload inválido ou ausente (HTTP 400). `campo` diz qual."""

    def __init__(self, mensagem, campo):
        super().__init__(mensagem)
        self.campo = campo


class DocumentoDuplicado(Exception):
    """Já existe documento com o mesmo identificador e revisão (HTTP 409)."""


# -- leitura dos campos ---------------------------------------------------
def _valor(dados, campo):
    valor = dados.get(campo)
    return valor.strip() if isinstance(valor, str) else ""


def _texto(dados, campo, rotulo, tamanho_maximo, obrigatorio=True):
    valor = _valor(dados, campo)
    if not valor:
        if obrigatorio:
            raise DadosInvalidos(f"Informe {rotulo}.", campo)
        return None
    if len(valor) > tamanho_maximo:
        raise DadosInvalidos(
            f"{rotulo.capitalize()} deve ter no máximo {tamanho_maximo} caracteres.",
            campo,
        )
    return valor


def _data(dados, campo, rotulo, obrigatorio=True):
    valor = _valor(dados, campo)
    if not valor:
        if obrigatorio:
            raise DadosInvalidos(f"Informe {rotulo}.", campo)
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        raise DadosInvalidos(
            f"{rotulo.capitalize()} é inválida. Use o formato AAAA-MM-DD.", campo
        )


def _opcao(queryset, valor, campo, rotulo):
    """Registro do `queryset` com pk = `valor`, ou DadosInvalidos."""
    if not valor:
        raise DadosInvalidos(f"Informe {rotulo}.", campo)
    try:
        return queryset.get(pk=valor)
    except (queryset.model.DoesNotExist, ValueError, TypeError, ValidationError):
        raise DadosInvalidos(f"Valor inválido para {rotulo}.", campo)


def _areas(dados):
    principal = _opcao(
        Area.objects.filter(ativo=True),
        _valor(dados, "area_principal"),
        "area_principal",
        "a área principal",
    )
    secundarias = []
    for valor in dados.getlist("areas_secundarias"):
        valor = valor.strip()
        if not valor:
            continue
        area = _opcao(
            Area.objects.filter(ativo=True),
            valor,
            "areas_secundarias",
            "as áreas secundárias",
        )
        if area.pk == principal.pk:
            raise DadosInvalidos(
                "A área principal não pode ser também uma área secundária.",
                "areas_secundarias",
            )
        if area not in secundarias:
            secundarias.append(area)
    return principal, secundarias


def _subcategoria(dados):
    categoria = _opcao(
        Categoria.objects.filter(ativo=True),
        _valor(dados, "categoria"),
        "categoria",
        "a categoria",
    )
    subcategoria = _opcao(
        Subcategoria.objects.filter(ativo=True),
        _valor(dados, "subcategoria"),
        "subcategoria",
        "a subcategoria",
    )
    if subcategoria.categoria_id != categoria.pk:
        raise DadosInvalidos(
            "A subcategoria não pertence à categoria escolhida.", "subcategoria"
        )
    return subcategoria


def _status(dados):
    valor = _valor(dados, "status").upper()
    if not valor:
        raise DadosInvalidos("Informe o status do documento.", "status")
    if valor not in StatusDocumentoChoices.values:
        raise DadosInvalidos(
            "Status inválido. Use: " + ", ".join(StatusDocumentoChoices.values) + ".",
            "status",
        )
    return valor


def _responsavel(dados):
    """Perfil de um usuário ativo, o mesmo critério de GET /api/responsaveis/."""
    perfil = _opcao(
        PerfilOperacional.objects.all(),
        _valor(dados, "responsavel"),
        "responsavel",
        "o responsável pelo documento",
    )
    ativo = (
        Usuario.objects.using("credenciais_db")
        .filter(pk=perfil.pk, ativo=True)
        .exists()
    )
    if not ativo:
        raise DadosInvalidos(
            "Valor inválido para o responsável pelo documento.", "responsavel"
        )
    return perfil


def _arquivo(arquivo):
    if not arquivo:
        raise DadosInvalidos("Envie o arquivo original do documento.", "arquivo")
    if Path(arquivo.name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise DadosInvalidos("Tipo de arquivo não permitido. Envie um PDF.", "arquivo")
    if arquivo.size == 0:
        raise DadosInvalidos("O arquivo enviado está vazio.", "arquivo")
    if arquivo.size > MAX_FILE_SIZE:
        raise DadosInvalidos(
            "O arquivo ultrapassa o limite máximo de 10 MB.", "arquivo"
        )
    arquivo.seek(0)
    inicio = arquivo.read(len(ASSINATURA_PDF))
    arquivo.seek(0)
    if inicio != ASSINATURA_PDF:
        raise DadosInvalidos("O arquivo enviado não é um PDF válido.", "arquivo")
    return arquivo


def validar(dados, arquivo) -> dict:
    """Valida os 16 campos na ordem da lista e devolve os valores prontos
    para gravar. `dados` é o request.POST (QueryDict)."""
    campos = {
        "titulo": _texto(dados, "titulo", "o título", 300),
        "identificador_codigo": _texto(
            dados, "identificador_codigo", "o identificador ou código", 100
        ),
        "tipo_documento": _opcao(
            TipoDocumento.objects.all(),
            _valor(dados, "tipo_documento"),
            "tipo_documento",
            "o tipo de documento",
        ),
        "numero_revisao": _texto(dados, "numero_revisao", "o número da revisão", 30),
        "data_emissao": _data(dados, "data_emissao", "a data de emissão"),
        "data_vigencia": _data(
            dados, "data_vigencia", "a data de vigência", obrigatorio=False
        ),
    }
    campos["area_principal"], campos["areas_secundarias"] = _areas(dados)
    campos["subcategoria"] = _subcategoria(dados)
    campos["status"] = _status(dados)
    campos["nivel_sigilo"] = _opcao(
        NivelSigilo.objects.all(),
        _valor(dados, "nivel_sigilo"),
        "nivel_sigilo",
        "o nível de sigilo",
    )
    campos["origem_fonte"] = _texto(dados, "origem_fonte", "a origem ou fonte", 200)
    campos["responsavel"] = _responsavel(dados)
    campos["idioma"] = _opcao(
        Idioma.objects.all(), _valor(dados, "idioma"), "idioma", "o idioma"
    )
    campos["referencia_normativa"] = _texto(
        dados,
        "referencia_normativa",
        "a norma ou documento relacionado",
        TAMANHO_MAXIMO_REFERENCIA,
        obrigatorio=False,
    )
    campos["data_validade"] = _data(
        dados, "data_validade", "a data de validade", obrigatorio=False
    )
    _arquivo(arquivo)

    emissao = campos["data_emissao"]
    if campos["data_vigencia"] and campos["data_vigencia"] < emissao:
        raise DadosInvalidos(
            "A data de vigência não pode ser anterior à data de emissão.",
            "data_vigencia",
        )
    if campos["data_validade"] and campos["data_validade"] < emissao:
        raise DadosInvalidos(
            "A data de validade não pode ser anterior à data de emissão.",
            "data_validade",
        )
    return campos


# -- gravação -------------------------------------------------------------
def _ja_existe(identificador, revisao) -> bool:
    return Documento.objects.filter(
        identificador_codigo=identificador, numero_revisao=revisao
    ).exists()


def _gravar_arquivo(arquivo) -> Path:
    """Grava o PDF com nome uuid na pasta privada. Arquivo pela metade (disco
    cheio, por exemplo) é apagado antes de repassar o erro."""
    pasta = Path(settings.DOCUMENTOS_UPLOAD_DIR)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{uuid.uuid4().hex}.pdf"
    try:
        with open(caminho, "xb") as destino:
            for pedaco in arquivo.chunks():
                destino.write(pedaco)
    except OSError:
        caminho.unlink(missing_ok=True)
        raise
    return caminho


def criar(dados, arquivo, usuario_upload) -> Documento:
    """
    Valida, grava o arquivo e cria o documento AGUARDANDO_VALIDACAO com a
    linha ENVIADO no histórico. `usuario_upload` é o PerfilOperacional de
    quem chamou (vem do JWT).

    Levanta DadosInvalidos, DocumentoDuplicado, OSError (falha ao gravar o
    arquivo) ou DatabaseError (falha do banco, já sem o arquivo na pasta).
    """
    campos = validar(dados, arquivo)
    identificador = campos["identificador_codigo"]
    revisao = campos["numero_revisao"]
    if _ja_existe(identificador, revisao):
        raise DocumentoDuplicado(
            "Já existe um documento com este identificador e esta revisão."
        )

    caminho = _gravar_arquivo(arquivo)
    try:
        # O commit acontece na saída do bloco. É ali que os triggers
        # deferrable de área principal (0003) checam o documento, então
        # qualquer falha deles também cai no except abaixo.
        with transaction.atomic():
            documento = Documento.objects.create(
                titulo=campos["titulo"],
                identificador_codigo=identificador,
                tipo_documento=campos["tipo_documento"],
                numero_revisao=revisao,
                data_emissao=campos["data_emissao"],
                data_vigencia=campos["data_vigencia"],
                data_validade=campos["data_validade"],
                subcategoria=campos["subcategoria"],
                status=campos["status"],
                # O nível final começa igual ao sugerido; quem aprova decide.
                nivel_sigilo=campos["nivel_sigilo"],
                nivel_sigilo_sugerido=campos["nivel_sigilo"],
                origem_fonte=campos["origem_fonte"],
                responsavel=campos["responsavel"],
                idioma=campos["idioma"],
                referencia_normativa=campos["referencia_normativa"],
                arquivo_original_url=caminho.name,
                usuario_upload=usuario_upload,
                status_validacao=StatusValidacao.AGUARDANDO_VALIDACAO,
            )
            DocumentoArea.objects.create(
                documento=documento, area=campos["area_principal"], is_principal=True
            )
            DocumentoArea.objects.bulk_create(
                DocumentoArea(documento=documento, area=area, is_principal=False)
                for area in campos["areas_secundarias"]
            )
            HistoricoValidacao.objects.create(
                documento=documento,
                acao=AcaoValidacao.ENVIADO,
                usuario=usuario_upload,
            )
    except BaseException as erro:
        caminho.unlink(missing_ok=True)
        # Dois envios iguais ao mesmo tempo passam juntos pela checagem acima;
        # o segundo esbarra na constraint única do banco.
        if isinstance(erro, IntegrityError) and _ja_existe(identificador, revisao):
            raise DocumentoDuplicado(
                "Já existe um documento com este identificador e esta revisão."
            ) from erro
        raise
    return documento
