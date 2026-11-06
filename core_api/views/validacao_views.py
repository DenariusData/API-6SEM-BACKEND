"""
Endpoints de validação de documento (S2-34).

    POST /api/documents/<id>/aprovar/   {"nivel_sigilo"?, "confirmar_confidencial"?}
    POST /api/documents/<id>/devolver/  {"comentario"}

Esta camada só faz HTTP: autentica (JWT, papel ADMINISTRADOR), lê o corpo e
traduz as exceções da service em status. As regras do fluxo (autor, estado,
comentário, confirmação de Confidencial) ficam em services/validacao.py.

Status: 400 dados inválidos · 401 sem token · 403 sem papel/autor ·
404 documento inexistente · 409 documento já decidido.
"""

import json
import uuid

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core_api.models import Documento, PerfilOperacional
from core_api.services import validacao
from core_api.utils.jwt_auth import require_role
from credenciais.models import Papel


def _erro(detalhe, status, campo=None):
    corpo = {"detail": detalhe}
    if campo:
        corpo["field"] = campo
    return JsonResponse(corpo, status=status)


def _ler_corpo(request):
    """Corpo JSON como dict. Corpo vazio vale {}. Levanta ValueError se inválido."""
    if not request.body:
        return {}
    try:
        dados = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError("Dados de requisição inválidos.") from e
    if not isinstance(dados, dict):
        raise ValueError("Dados de requisição inválidos.")
    return dados


def _perfil_de(request):
    """PerfilOperacional de quem chamou (o `sub` do JWT é o `usuario_id`)."""
    try:
        return PerfilOperacional.objects.get(
            pk=uuid.UUID(str(request.auth_user["sub"]))
        )
    except (PerfilOperacional.DoesNotExist, ValueError, KeyError):
        return None


def _serializar(doc):
    return {
        "id": str(doc.id),
        "status_validacao": doc.status_validacao,
        "nivel_sigilo": doc.nivel_sigilo.nome,
        "nivel_sigilo_sugerido": doc.nivel_sigilo_sugerido.nome,
        "validado_por": str(doc.validado_por_id) if doc.validado_por_id else None,
        "validado_em": doc.validado_em.isoformat() if doc.validado_em else None,
        "comentario_devolucao": doc.comentario_devolucao,
    }


def _executar(request, documento_id, acao):
    """Fluxo comum: perfil → corpo → service → resposta."""
    perfil = _perfil_de(request)
    if perfil is None:
        return _erro("Perfil operacional do usuário não encontrado.", 403)
    try:
        corpo = _ler_corpo(request)
    except ValueError as e:
        return _erro(str(e), 400)

    papel = request.auth_user.get("role")
    try:
        if acao == "aprovar":
            doc = validacao.aprovar(
                documento_id,
                perfil,
                papel,
                nivel_sigilo=corpo.get("nivel_sigilo"),
                confirmar_confidencial=corpo.get("confirmar_confidencial", False),
            )
        else:
            doc = validacao.devolver(
                documento_id, perfil, papel, corpo.get("comentario")
            )
    except Documento.DoesNotExist:
        return _erro("Documento não encontrado.", 404)
    except validacao.PermissaoNegada as e:
        return _erro(str(e), 403)
    except validacao.TransicaoInvalida as e:
        return _erro(str(e), 409)
    except validacao.DadosInvalidos as e:
        return _erro(str(e), 400, e.campo)

    return JsonResponse(_serializar(doc), status=200)


@csrf_exempt
@require_http_methods(["POST"])
@require_role(Papel.ADMINISTRADOR)
def aprovar_documento_view(request, documento_id):
    """POST /api/documents/<id>/aprovar/"""
    return _executar(request, documento_id, "aprovar")


@csrf_exempt
@require_http_methods(["POST"])
@require_role(Papel.ADMINISTRADOR)
def devolver_documento_view(request, documento_id):
    """POST /api/documents/<id>/devolver/"""
    return _executar(request, documento_id, "devolver")
