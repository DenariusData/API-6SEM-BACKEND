"""
Serviço de Autenticação SSO e Gestão de Usuários (core_api.services.auth.autenticacao).
"""

from typing import Dict, List, Optional
from core_api.models.usuario import PerfilUsuario, Usuario

# Usuários de Demonstração pré-cadastrados
DEMO_USERS_DB: List[Dict] = [
    {
        "id": 1,
        "email": "engenharia@akaer.com.br",
        "password": "eng123",
        "name": "Carlos Eduardo",
        "role": PerfilUsuario.ENGENHARIA,
        "matricula": "AK-90822",
        "cargo": "Engenheiro Aeroespacial Senior",
        "avatar": "/avatars/carlos.png",
        "allowed_menus": [
            "Início",
            "Pesquisa Avançada",
            "Documentos",
            "Projetos",
            "AI Command Assistant",
            "Solicitar OI",
        ],
    },
    {
        "id": 2,
        "email": "qualidade@akaer.com.br",
        "password": "qual123",
        "name": "Ana Souza",
        "role": PerfilUsuario.QUALIDADE,
        "matricula": "AK-77401",
        "cargo": "Inspectora de Qualidade e Conformidade",
        "avatar": "/avatars/ana.png",
        "allowed_menus": [
            "Início",
            "Pesquisa Avançada",
            "Documentos",
            "Relatórios de Qualidade",
            "Auditoria & Conformidade",
        ],
    },
    {
        "id": 3,
        "email": "admin@akaer.com.br",
        "password": "admin123",
        "name": "Ricardo Mendes",
        "role": PerfilUsuario.ADMINISTRADOR,
        "matricula": "AK-10001",
        "cargo": "Administrador do Sistema",
        "avatar": "/avatars/ricardo.png",
        "allowed_menus": [
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
    },
]


class AuthService:
    """
    Serviço central de autenticação e gerenciamento de perfis de usuário.
    """

    @classmethod
    def get_all_users(cls) -> List[Dict]:
        """Retorna todos os usuários de demonstração cadastrados (sem a senha)."""
        return [cls._sanitize_user(u) for u in DEMO_USERS_DB]

    @classmethod
    def find_by_email(cls, email: str) -> Optional[Dict]:
        """Busca usuário por e-mail (case-insensitive)."""
        clean_email = email.strip().lower()
        for user in DEMO_USERS_DB:
            if user["email"].lower() == clean_email:
                return user
        return None

    @classmethod
    def find_by_id(cls, user_id: int) -> Optional[Dict]:
        """Busca usuário por ID."""
        for user in DEMO_USERS_DB:
            if user["id"] == user_id:
                return cls._sanitize_user(user)
        return None

    @classmethod
    def authenticate(cls, email: str, password: str) -> Optional[Dict]:
        """
        Autentica o usuário pelo e-mail e senha.
        Retorna o dicionário sanitizado do usuário ou None se inválido.
        """
        user = cls.find_by_email(email)
        if user and user["password"] == password:
            return cls._sanitize_user(user)
        return None

    @classmethod
    def create_user(cls, user_data: Dict) -> Dict:
        """Cadastra um novo usuário no repositório de memória."""
        new_id = max([u["id"] for u in DEMO_USERS_DB], default=0) + 1
        new_user = {
            "id": new_id,
            "email": user_data.get("email", "").strip().lower(),
            "password": user_data.get("password", "akaer123"),
            "name": user_data.get("name", "Novo Usuário"),
            "role": user_data.get("role", PerfilUsuario.ENGENHARIA),
            "matricula": user_data.get("matricula", f"AK-{new_id:05d}"),
            "cargo": user_data.get("cargo", "Colaborador Akaer"),
            "avatar": user_data.get("avatar", "/avatars/default.png"),
            "allowed_menus": user_data.get("allowed_menus", ["Início", "Documentos"]),
        }
        DEMO_USERS_DB.append(new_user)
        return cls._sanitize_user(new_user)

    @classmethod
    def _sanitize_user(cls, user: Dict) -> Dict:
        """Remove a senha do dicionário antes de retornar à API."""
        user_copy = user.copy()
        user_copy.pop("password", None)
        return user_copy


# Alias para retrocompatibilidade
DemoUserService = AuthService
