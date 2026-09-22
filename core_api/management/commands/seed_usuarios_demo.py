"""
Popula os bancos "credenciais_db" e "default" com os usuários de demonstração
que antes viviam em memória (DEMO_USERS_DB), agora como registros reais
nos dois bancos.

Uso:
    python manage.py migrate            # cria as tabelas nos dois bancos
    python manage.py seed_usuarios_demo  # cria os usuários de exemplo
"""

from django.core.management.base import BaseCommand

from core_api.services.auth.autenticacao import AuthService
from credenciais.models import Papel, Usuario

USUARIOS_DEMO = [
    {
        "email": "engenharia@akaer.com.br",
        "password": "eng123",
        "name": "Carlos Eduardo",
        "role": Papel.ENGENHARIA,
        "matricula": "AK-90822",
        "cargo": "Engenheiro Aeroespacial Senior",
        "avatar": "/avatars/carlos.png",
    },
    {
        "email": "qualidade@akaer.com.br",
        "password": "qual123",
        "name": "Ana Souza",
        "role": Papel.QUALIDADE,
        "matricula": "AK-77401",
        "cargo": "Inspectora de Qualidade e Conformidade",
        "avatar": "/avatars/ana.png",
    },
    {
        "email": "admin@akaer.com.br",
        "password": "admin123",
        "name": "Ricardo Mendes",
        "role": Papel.ADMINISTRADOR,
        "matricula": "AK-10001",
        "cargo": "Administrador do Sistema",
        "avatar": "/avatars/ricardo.png",
    },
]


class Command(BaseCommand):
    help = "Cria os usuários de demonstração (idempotente)."

    def handle(self, *args, **options):
        for dados in USUARIOS_DEMO:
            if (
                Usuario.objects.using("credenciais_db")
                .filter(email=dados["email"])
                .exists()
            ):
                self.stdout.write(f"- já existe: {dados['email']}")
                continue
            AuthService.create_user(dados)
            self.stdout.write(self.style.SUCCESS(f"+ criado: {dados['email']}"))
