"""
Modelos de dados PESSOAIS/SENSÍVEIS (autenticação).

Estes modelos residem no banco "credenciais_db" (ver api/settings.py e
credenciais/db_router.py), fisicamente separado do banco de negócio, para que
um comprometimento do banco de negócio não exponha credenciais nem dados
pessoais, e vice-versa.

Estrutura extraída da modelagem (tabela `usuario`):

    id                   uuid            PK
    nome                 varchar(150)
    email                varchar(180)
    papel                varchar(20)     FK -> papel.codigo
    senha_hash           varchar(255)
    ultimo_login_em      timestamptz     (nullable)
    tentativas_login_f   int
    bloqueado_ate        timestamptz     (nullable)
    ativo                boolean
    criado_em            timestamptz
"""

import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class Papel(models.Model):
    """
    Tabela de referência dos papéis/perfis de acesso (Engenharia, Qualidade,
    Administrador, ...). Mantida no mesmo banco de `Usuario` pois a FK
    `usuario.papel` só pode ser uma constraint real dentro do mesmo banco.
    """

    ENGENHARIA = "ENGENHARIA"
    QUALIDADE = "QUALIDADE"
    ADMINISTRADOR = "ADMINISTRADOR"

    codigo = models.CharField(max_length=20, primary_key=True)
    descricao = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        app_label = "credenciais"
        db_table = "papel"
        verbose_name = "Papel"
        verbose_name_plural = "Papéis"

    def __str__(self) -> str:
        return self.codigo


class Usuario(models.Model):
    """
    Dados de identidade e credenciais do usuário. NÃO contém dados
    operacionais (matrícula, cargo, avatar, menus liberados) — esses ficam
    no banco de negócio, em um perfil operacional que referencia `id`
    (uuid) por valor, sem FK física entre bancos.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(max_length=150)
    email = models.CharField(max_length=180, unique=True)
    papel = models.ForeignKey(
        Papel,
        to_field="codigo",
        db_column="papel",
        on_delete=models.PROTECT,
        related_name="usuarios",
    )
    senha_hash = models.CharField(max_length=255)
    ultimo_login_em = models.DateTimeField(null=True, blank=True)
    tentativas_login_falhas = models.IntegerField(
        default=0, db_column="tentativas_login_f"
    )
    bloqueado_ate = models.DateTimeField(null=True, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "credenciais"
        db_table = "usuario"
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"

    def __str__(self) -> str:
        return f"{self.nome} <{self.email}>"

    # -- Senha ---------------------------------------------------------
    def set_senha(self, senha_plana: str) -> None:
        """Gera e armazena o hash da senha (nunca armazenar texto plano)."""
        self.senha_hash = make_password(senha_plana)

    def verificar_senha(self, senha_plana: str) -> bool:
        return check_password(senha_plana, self.senha_hash)

    # -- Controle de tentativas / bloqueio ------------------------------
    @property
    def esta_bloqueado(self) -> bool:
        return bool(self.bloqueado_ate and self.bloqueado_ate > timezone.now())

    def registrar_falha_login(self, max_tentativas: int, minutos_bloqueio: int) -> None:
        self.tentativas_login_falhas += 1
        if self.tentativas_login_falhas >= max_tentativas:
            self.bloqueado_ate = timezone.now() + timezone.timedelta(
                minutes=minutos_bloqueio
            )
        self.save(
            using="credenciais_db",
            update_fields=["tentativas_login_falhas", "bloqueado_ate"],
        )

    def registrar_login_sucesso(self) -> None:
        self.tentativas_login_falhas = 0
        self.bloqueado_ate = None
        self.ultimo_login_em = timezone.now()
        self.save(
            using="credenciais_db",
            update_fields=[
                "tentativas_login_falhas",
                "bloqueado_ate",
                "ultimo_login_em",
            ],
        )
