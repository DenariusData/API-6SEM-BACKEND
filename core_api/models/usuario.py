"""
Modelo/Estrutura de dados de Usuário e Perfis de Acesso.
"""

from dataclasses import dataclass, field
from typing import List


class PerfilUsuario:
    ENGENHARIA = "Engenharia"
    QUALIDADE = "Qualidade"
    ADMINISTRADOR = "Administrador"


@dataclass
class Usuario:
    id: int
    email: str
    name: str
    role: str
    matricula: str
    cargo: str
    avatar: str = "/avatars/default.png"
    allowed_menus: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "matricula": self.matricula,
            "cargo": self.cargo,
            "avatar": self.avatar,
            "allowed_menus": self.allowed_menus,
        }
