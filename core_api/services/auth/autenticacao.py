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

import unicodedata
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from core_api.models import (
    AcaoAuditoria,
    Area,
    EntidadeAuditoria,
    NivelSigilo,
    PerfilOperacional,
    UsuarioPermissaoArea,
)
from core_api.services.auditoria import registrar_acao
from credenciais.models import Papel, Usuario

MAX_TENTATIVAS_LOGIN = 5
MINUTOS_BLOQUEIO = 15

ERRO_CREDENCIAIS_INVALIDAS = "credenciais_invalidas"
ERRO_USUARIO_INATIVO = "usuario_inativo"
ERRO_USUARIO_BLOQUEADO = "usuario_bloqueado"

PAPEIS_VALIDOS = (Papel.ENGENHARIA, Papel.QUALIDADE, Papel.ADMINISTRADOR)

# Permissões de área que todo usuário novo recebe pelo papel, iguais às dos
# usuários de demonstração. Sem isso o usuário cadastrado pela tela não
# enxerga documento nenhum (acesso é usuário + área + sigilo, sem herança).
# O administrador ajusta depois, se precisar.
PERMISSOES_PADRAO_POR_PAPEL = {
    Papel.ENGENHARIA: [
        ("Manutenção", "RESTRITO"),
        ("Operações", "INTERNO"),
        ("Segurança", "PUBLICO"),
    ],
    Papel.QUALIDADE: [
        ("Compliance", "CONFIDENCIAL"),
        ("Segurança", "INTERNO"),
        ("Manutenção", "PUBLICO"),
    ],
    Papel.ADMINISTRADOR: [
        ("Manutenção", "CONFIDENCIAL"),
        ("Operações", "CONFIDENCIAL"),
        ("Segurança", "CONFIDENCIAL"),
        ("Compliance", "CONFIDENCIAL"),
    ],
}


class CadastroInvalido(ValueError):
    """Dado de cadastro recusado (papel desconhecido, e-mail repetido)."""


def normalizar_papel(valor) -> str:
    """Aceita o código ("ENGENHARIA") ou o nome mostrado na tela
    ("Engenharia", "administrador"), sem diferenciar maiúscula nem acento."""
    texto = unicodedata.normalize("NFKD", str(valor or "")).strip().upper()
    codigo = "".join(c for c in texto if not unicodedata.combining(c))
    if codigo not in PAPEIS_VALIDOS:
        raise CadastroInvalido(
            "Perfil inválido. Use Engenharia, Qualidade ou Administrador."
        )
    return codigo


MENUS_PADRAO_POR_PAPEL = {
    Papel.ENGENHARIA: [
        "Início",
        "Pesquisa Avançada",
        "Documentos",
        "Projetos",
        "AI Command Assistant",
        "Solicitar OI",
    ],
    Papel.QUALIDADE: [
        "Início",
        "Pesquisa Avançada",
        "Documentos",
        "Relatórios de Qualidade",
        "Auditoria & Conformidade",
    ],
    Papel.ADMINISTRADOR: [
        "Início",
        "Pesquisa Avançada",
        "Documentos",
        "Projetos",
        "Despachos",
        "Malotes Digitais",
        "Gestão de Usuários",
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

        Toda tentativa vai para o log de auditoria (AKA-31) so com o id do
        usuario, nunca com o e-mail digitado. E-mail que nao existe fica
        como LOGIN_FALHOU sem usuario.
        """
        usuario, erro = cls._verificar_credenciais(email, senha)
        registrar_acao(
            usuario.id if usuario else None,
            AcaoAuditoria.LOGIN_FALHOU if erro else AcaoAuditoria.LOGIN,
            EntidadeAuditoria.USUARIO,
            usuario.id if usuario else None,
        )
        if erro:
            return None, erro
        return cls._compor_usuario(usuario), None

    @classmethod
    def _verificar_credenciais(
        cls, email: str, senha: str
    ) -> Tuple[Optional[Usuario], Optional[str]]:
        """(usuario, None) no sucesso; (usuario ou None, codigo_do_erro) na
        falha. O usuario vem junto na falha para o log saber de quem foi."""
        usuario = cls.find_by_email(email)
        if usuario is None:
            return None, ERRO_CREDENCIAIS_INVALIDAS

        if usuario.esta_bloqueado:
            return usuario, ERRO_USUARIO_BLOQUEADO

        if not usuario.ativo:
            return usuario, ERRO_USUARIO_INATIVO

        if not usuario.verificar_senha(senha):
            usuario.registrar_falha_login(MAX_TENTATIVAS_LOGIN, MINUTOS_BLOQUEIO)
            if usuario.esta_bloqueado:
                return usuario, ERRO_USUARIO_BLOQUEADO
            return usuario, ERRO_CREDENCIAIS_INVALIDAS

        usuario.registrar_login_sucesso()
        return usuario, None

    # -- Cadastro ------------------------------------------------------
    @classmethod
    def create_user(cls, user_data: Dict) -> Dict:
        """
        Cadastra um novo usuario. Grava:
          - credenciais/identidade em `credenciais_db` (Usuario)
          - dados operacionais em `default` (PerfilOperacional)
          - permissoes de area padrao do papel (UsuarioPermissaoArea)

        Levanta CadastroInvalido para papel desconhecido ou e-mail ja usado.
        """
        papel_codigo = normalizar_papel(user_data.get("role") or Papel.ENGENHARIA)
        email = (user_data.get("email") or "").strip().lower()
        if Usuario.objects.using("credenciais_db").filter(email__iexact=email).exists():
            raise CadastroInvalido("Já existe um usuário com esse e-mail.")

        papel, _ = Papel.objects.using("credenciais_db").get_or_create(
            codigo=papel_codigo, defaults={"descricao": papel_codigo.title()}
        )

        usuario = Usuario(
            nome=user_data.get("name", "Novo Usuario"),
            email=email,
            papel=papel,
        )
        usuario.set_senha(user_data.get("password") or "akaer123")
        usuario.save(using="credenciais_db")

        total_usuarios = Usuario.objects.using("credenciais_db").count()
        perfil = PerfilOperacional.objects.using("default").create(
            usuario_id=usuario.id,
            matricula=user_data.get("matricula") or f"AK-{total_usuarios:05d}",
            cargo=user_data.get("cargo", "Colaborador Akaer"),
            avatar=user_data.get("avatar", "/avatars/default.png"),
            allowed_menus=user_data.get(
                "allowed_menus", MENUS_PADRAO_POR_PAPEL.get(papel_codigo, ["Início"])
            ),
        )
        cls._conceder_permissoes_padrao(perfil, papel_codigo)

        return cls._compor_usuario(usuario)

    @staticmethod
    def _conceder_permissoes_padrao(perfil, papel_codigo: str) -> None:
        """Area ou nivel que ainda nao existe (sem seed_taxonomia) fica de fora,
        sem erro: o cadastro continua valendo e o admin concede depois."""
        regras = PERMISSOES_PADRAO_POR_PAPEL.get(papel_codigo, [])
        areas = {
            a.nome: a for a in Area.objects.filter(nome__in=[r[0] for r in regras])
        }
        niveis = {
            n.nome: n
            for n in NivelSigilo.objects.filter(nome__in=[r[1] for r in regras])
        }
        for nome_area, nome_nivel in regras:
            if nome_area in areas and nome_nivel in niveis:
                UsuarioPermissaoArea.objects.get_or_create(
                    usuario=perfil,
                    area=areas[nome_area],
                    defaults={"nivel_sigilo_maximo": niveis[nome_nivel]},
                )

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
            "role": usuario.papel.descricao,
            "role_code": usuario.papel_id,
            "email_pendente": usuario.email_pendente,
            "pode_validar": usuario.papel_id == Papel.ADMINISTRADOR,
            "matricula": perfil.matricula if perfil else None,
            "cargo": perfil.cargo if perfil else "",
            "avatar": perfil.avatar if perfil else "/avatars/default.png",
            "allowed_menus": perfil.allowed_menus if perfil else ["Início"],
            "ativo": usuario.ativo,
            "ultimo_login_em": (
                usuario.ultimo_login_em.isoformat() if usuario.ultimo_login_em else None
            ),
        }


# Alias para retrocompatibilidade
DemoUserService = AuthService
