"""
Servico de Autenticacao e Gestao de Usuarios (core_api.services.auth.autenticacao).

Este servico e o unico ponto da aplicacao que "compoe" dados vindos dos
dois bancos:

    credenciais_db (app `credenciais`)    -> identidade + credenciais (Usuario, Papel)
    default   (app `core_api`)  -> dados operacionais (PerfilOperacional)

Nao existe JOIN entre os bancos (isso nao e possivel fisicamente); a
composicao e feita aqui, em Python, buscando primeiro no banco de
credenciais e depois, so se necessario, no banco de negocio -- nunca o
contrario, para que rotinas de negocio nao precisem tocar em dados
pessoais.
"""

from typing import Dict, List, Optional, Tuple
from uuid import UUID

from core_api.models import PerfilOperacional
from credenciais.models import Papel, Usuario

MAX_TENTATIVAS_LOGIN = 5
MINUTOS_BLOQUEIO = 10

ERRO_CREDENCIAIS_INVALIDAS = "credenciais_invalidas"
ERRO_USUARIO_INATIVO = "usuario_inativo"
ERRO_USUARIO_BLOQUEADO = "usuario_bloqueado"

MENUS_PADRAO_POR_PAPEL = {
    Papel.ENGENHARIA: [
        "Inicio",
        "Pesquisa Avancada",
        "Documentos",
        "Projetos",
        "AI Command Assistant",
        "Solicitar OI",
    ],
    Papel.QUALIDADE: [
        "Inicio",
        "Pesquisa Avancada",
        "Documentos",
        "Relatorios de Qualidade",
        "Auditoria & Conformidade",
    ],
    Papel.ADMINISTRADOR: [
        "Inicio",
        "Pesquisa Avancada",
        "Documentos",
        "Projetos",
        "Despachos",
        "Malotes Digitais",
        "Gestao de Usuarios",
        "Importar Arquivos",
        "Classificar Categorias",
        "AI Command Assistant",
    ],
}


class AuthService:
    """Servico central de autenticacao e gerenciamento de perfis de usuario."""

    # -- Leitura ---------------------------------------------------------
    @classmethod
    def get_all_users(cls) -> List[Dict]:
        usuarios = Usuario.objects.using("credenciais_db").select_related("papel").all()
        return [cls._compor_usuario(u) for u in usuarios]

    @classmethod
    def find_by_id(cls, usuario_id) -> Optional[Dict]:
        try:
            usuario_uuid = UUID(str(usuario_id))
        except (ValueError, AttributeError, TypeError):
            return None
        usuario = (
            Usuario.objects.using("credenciais_db")
            .select_related("papel")
            .filter(id=usuario_uuid)
            .first()
        )
        return cls._compor_usuario(usuario) if usuario else None

    @classmethod
    def find_by_email(cls, email: str) -> Optional[Usuario]:
        clean_email = email.strip().lower()
        return (
            Usuario.objects.using("credenciais_db")
            .select_related("papel")
            .filter(email__iexact=clean_email)
            .first()
        )

    # -- Autenticacao ------------------------------------------------------
    @classmethod
    def authenticate(
        cls, email: str, senha: str
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Autentica o usuario pelo e-mail e senha.

        Retorna (usuario_dict, None) em caso de sucesso, ou
        (None, codigo_do_erro) em caso de falha. O codigo de erro serve
        apenas para a view decidir a mensagem/telemetria; a view NAO deve
        expor ao cliente se o e-mail existe ou nao (mensagem unificada).
        """
        usuario = cls.find_by_email(email)
        if usuario is None:
            return None, ERRO_CREDENCIAIS_INVALIDAS

        if usuario.esta_bloqueado:
            return None, ERRO_USUARIO_BLOQUEADO

        if not usuario.ativo:
            return None, ERRO_USUARIO_INATIVO

        if not usuario.verificar_senha(senha):
            usuario.registrar_falha_login(MAX_TENTATIVAS_LOGIN, MINUTOS_BLOQUEIO)
            if usuario.esta_bloqueado:
                return None, ERRO_USUARIO_BLOQUEADO
            return None, ERRO_CREDENCIAIS_INVALIDAS

        usuario.registrar_login_sucesso()
        return cls._compor_usuario(usuario), None

    # -- Cadastro ------------------------------------------------------
    @classmethod
    def create_user(cls, user_data: Dict) -> Dict:
        """
        Cadastra um novo usuario. Grava:
          - credenciais/identidade em `credenciais_db` (Usuario)
          - dados operacionais em `default` (PerfilOperacional)
        """
        papel_codigo = user_data.get("role", Papel.ENGENHARIA)
        papel, _ = Papel.objects.using("credenciais_db").get_or_create(
            codigo=papel_codigo, defaults={"descricao": papel_codigo.title()}
        )

        usuario = Usuario(
            nome=user_data.get("name", "Novo Usuario"),
            email=user_data.get("email", "").strip().lower(),
            papel=papel,
        )
        usuario.set_senha(user_data.get("password") or "akaer123")
        usuario.save(using="credenciais_db")

        total_usuarios = Usuario.objects.using("credenciais_db").count()
        PerfilOperacional.objects.using("default").create(
            usuario_id=usuario.id,
            matricula=user_data.get("matricula", f"AK-{total_usuarios:05d}"),
            cargo=user_data.get("cargo", "Colaborador Akaer"),
            avatar=user_data.get("avatar", "/avatars/default.png"),
            allowed_menus=user_data.get(
                "allowed_menus", MENUS_PADRAO_POR_PAPEL.get(papel_codigo, ["Inicio"])
            ),
        )

        return cls._compor_usuario(usuario)

    # -- Composicao entre os dois bancos ---------------------------------
    @classmethod
    def _compor_usuario(cls, usuario: Usuario) -> Dict:
        """
        Junta, em Python, o registro de credenciais (credenciais_db) com o
        perfil operacional (default). Nunca inclui `senha_hash`.
        """
        perfil = (
            PerfilOperacional.objects.using("default")
            .filter(usuario_id=usuario.id)
            .first()
        )

        return {
            "id": str(usuario.id),
            "email": usuario.email,
            "name": usuario.nome,
            "role": usuario.papel_id,
            "matricula": perfil.matricula if perfil else None,
            "cargo": perfil.cargo if perfil else "",
            "avatar": perfil.avatar if perfil else "/avatars/default.png",
            "allowed_menus": perfil.allowed_menus if perfil else ["Inicio"],
            "ativo": usuario.ativo,
            "ultimo_login_em": (
                usuario.ultimo_login_em.isoformat() if usuario.ultimo_login_em else None
            ),
        }


# Alias para retrocompatibilidade
DemoUserService = AuthService
