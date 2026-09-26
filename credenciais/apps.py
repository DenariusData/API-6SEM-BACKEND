"""
Configuração do app "credenciais".

Este app concentra exclusivamente os dados PESSOAIS/SENSÍVEIS ligados à
autenticação (usuário e papel/perfil de acesso). Ele é roteado, via
DATABASE_ROUTERS (ver credenciais/db_router.py), para um banco de dados
fisicamente separado do banco de negócio (core_api), seguindo a lógica de
isolamento de dados pessoais discutida no início do projeto:

    Banco "credenciais" (este app)      Banco "default" / negócio (core_api)
    ───────────────────────────    ─────────────────────────────────────
    usuario (id, nome, email,      documentos, projetos, perfil
    papel, senha_hash, controle    operacional (matrícula, cargo,
    de login/bloqueio)             allowed_menus) referenciando
                                    usuario_id (uuid) sem FK física
                                    entre bancos.
"""

from django.apps import AppConfig


class CredenciaisConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "credenciais"
    verbose_name = "Credenciais e Autenticação (dados pessoais)"
