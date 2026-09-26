"""
Views relacionadas ao gerenciamento e upload de documentos.
"""

from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".txt",
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@csrf_exempt
@require_http_methods(["POST"])
def upload_document_view(request):
    """
    Endpoint para upload de documentos técnicos.

    POST /api/documents/upload/

    Campos esperados:
        file: arquivo
        title: título do documento
        category: categoria
        description: descrição opcional
    """

    uploaded_file = request.FILES.get("file")

    title = request.POST.get("title", "").strip()
    category = request.POST.get("category", "").strip()
    description = request.POST.get("description", "").strip()

    # =====================================================
    # VALIDAÇÃO DO ARQUIVO
    # =====================================================

    if not uploaded_file:
        return JsonResponse(
            {
                "detail": "Nenhum arquivo foi enviado.",
                "field": "file",
            },
            status=400,
        )

    if not title:
        return JsonResponse(
            {
                "detail": "O título do documento é obrigatório.",
                "field": "title",
            },
            status=400,
        )

    if not category:
        return JsonResponse(
            {
                "detail": "A categoria do documento é obrigatória.",
                "field": "category",
            },
            status=400,
        )

    # =====================================================
    # VALIDAÇÃO DA EXTENSÃO
    # =====================================================

    extension = Path(uploaded_file.name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return JsonResponse(
            {
                "detail": (
                    "Tipo de arquivo não permitido. "
                    "Envie PDF, DOC, DOCX, XLS, XLSX ou TXT."
                ),
                "field": "file",
            },
            status=400,
        )

    # =====================================================
    # VALIDAÇÃO DO TAMANHO
    # =====================================================

    if uploaded_file.size > MAX_FILE_SIZE:
        return JsonResponse(
            {
                "detail": "O arquivo ultrapassa o limite máximo de 10 MB.",
                "field": "file",
            },
            status=400,
        )

    # =====================================================
    # DIRETÓRIO DE UPLOAD
    # =====================================================

    upload_directory = Path(settings.BASE_DIR) / "uploads"

    upload_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    # =====================================================
    # EVITA CONFLITO DE NOMES
    # =====================================================

    original_name = Path(uploaded_file.name).name
    file_path = upload_directory / original_name

    if file_path.exists():
        file_stem = file_path.stem
        file_suffix = file_path.suffix

        counter = 1

        while file_path.exists():
            new_name = (
                f"{file_stem}_{counter}{file_suffix}"
            )

            file_path = upload_directory / new_name
            counter += 1

    # =====================================================
    # SALVAMENTO
    # =====================================================

    try:
        with open(file_path, "wb+") as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)

    except OSError:
        return JsonResponse(
            {
                "detail": (
                    "Não foi possível salvar o arquivo "
                    "no servidor."
                )
            },
            status=500,
        )

    # =====================================================
    # RESPOSTA
    # =====================================================

    return JsonResponse(
        {
            "message": "Arquivo enviado com sucesso!",
            "document": {
                "name": original_name,
                "saved_name": file_path.name,
                "title": title,
                "category": category,
                "description": description,
                "size": uploaded_file.size,
                "extension": extension,
                "path": str(file_path),
                "status": "Pendente",
            },
        },
        status=201,
    )