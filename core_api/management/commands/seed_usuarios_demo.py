"""
Popula os bancos "credenciais_db" e "default" com os usuários de demonstração
que antes viviam em memória (DEMO_USERS_DB), agora como registros reais
nos dois bancos.

Também concede as permissões de acesso a documentos (S2-26), diferentes por
usuário de propósito: a MESMA pergunta na tela da IA traz fontes diferentes
para engenharia e qualidade. Precisa das áreas e níveis de sigilo do
seed_taxonomia.

Uso:
    python manage.py migrate            # cria as tabelas nos dois bancos
    python manage.py seed_taxonomia     # áreas e níveis de sigilo
    python manage.py seed_usuarios_demo  # cria os usuários e as permissões
"""

from django.core.management.base import BaseCommand

from core_api.models import Area, NivelSigilo, UsuarioPermissaoArea
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

# e-mail -> [(área, nível de sigilo máximo)]. Quem não aparece na lista de uma
# área não enxerga nenhum documento dela (sem herança por setor nem por papel).
PERMISSOES_DEMO = {
    "engenharia@akaer.com.br": [
        ("Manutenção", "RESTRITO"),
        ("Operações", "INTERNO"),
        ("Segurança", "PUBLICO"),
    ],
    "qualidade@akaer.com.br": [
        ("Compliance", "CONFIDENCIAL"),
        ("Segurança", "INTERNO"),
        ("Manutenção", "PUBLICO"),
    ],
    "admin@akaer.com.br": [
        ("Manutenção", "CONFIDENCIAL"),
        ("Operações", "CONFIDENCIAL"),
        ("Segurança", "CONFIDENCIAL"),
        ("Compliance", "CONFIDENCIAL"),
    ],
}


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
        self._conceder_permissoes()

    def _conceder_permissoes(self):
        """Idempotente e sem sobrescrever: permissão que já existe (mesmo com
        outro nível, ajustado depois) fica como está."""
        areas = {a.nome: a for a in Area.objects.all()}
        niveis = {n.nome: n for n in NivelSigilo.objects.all()}
        concedidas = 0
        for email, regras in PERMISSOES_DEMO.items():
            usuario = (
                Usuario.objects.using("credenciais_db").filter(email=email).first()
            )
            if usuario is None:
                continue
            for nome_area, nome_nivel in regras:
                if nome_area not in areas or nome_nivel not in niveis:
                    self.stdout.write(
                        self.style.WARNING(
                            f"! sem área/nível '{nome_area}'/'{nome_nivel}': "
                            "rode seed_taxonomia e este comando de novo"
                        )
                    )
                    continue
                _, criada = UsuarioPermissaoArea.objects.get_or_create(
                    usuario_id=usuario.id,
                    area=areas[nome_area],
                    defaults={"nivel_sigilo_maximo": niveis[nome_nivel]},
                )
                concedidas += int(criada)
        self.stdout.write(f"Permissões de acesso criadas: {concedidas}")
