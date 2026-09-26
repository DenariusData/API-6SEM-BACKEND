"""
Roteador de banco de dados para isolamento de dados pessoais.

Regra: todo modelo do app "credenciais" (dados pessoais/credenciais) vive
exclusivamente no banco "credenciais_db". Todos os demais apps (inclusive
django.contrib.*, admin, sessions etc.) continuam no "default", que é o
banco de negócio. Nenhuma migração de "credenciais" é aplicada no "default" e
vice-versa, e não são permitidas relações (FK) entre modelos dos dois
bancos — a composição de dados, quando necessária, é feita na camada de
serviço (ver core_api.services.auth.autenticacao), nunca via JOIN.
"""

CREDENCIAIS_APP_LABEL = "credenciais"
CREDENCIAIS_DB = "credenciais_db"
DEFAULT_DB = "default"


class CredenciaisRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == CREDENCIAIS_APP_LABEL:
            return CREDENCIAIS_DB
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == CREDENCIAIS_APP_LABEL:
            return CREDENCIAIS_DB
        return None

    def allow_relation(self, obj1, obj2, **hints):
        obj1_is_credenciais = obj1._meta.app_label == CREDENCIAIS_APP_LABEL
        obj2_is_credenciais = obj2._meta.app_label == CREDENCIAIS_APP_LABEL
        if obj1_is_credenciais or obj2_is_credenciais:
            # Só permite relação se AMBOS os objetos forem do app "credenciais".
            # Isso impede, por exemplo, uma FK acidental de um modelo de
            # negócio para `credenciais.Usuario`.
            return obj1_is_credenciais and obj2_is_credenciais
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == CREDENCIAIS_APP_LABEL:
            return db == CREDENCIAIS_DB
        if db == CREDENCIAIS_DB:
            return False
        return None
