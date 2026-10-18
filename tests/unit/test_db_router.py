from types import SimpleNamespace

import pytest

from credenciais.db_router import CredenciaisRouter

router = CredenciaisRouter()


def modelo(app_label):
    return SimpleNamespace(_meta=SimpleNamespace(app_label=app_label))


@pytest.mark.parametrize(
    "app_label, banco", [("credenciais", "credenciais_db"), ("core_api", None)]
)
def test_leitura_e_escrita_vao_para_o_banco_certo(app_label, banco):
    assert router.db_for_read(modelo(app_label)) == banco
    assert router.db_for_write(modelo(app_label)) == banco


@pytest.mark.parametrize(
    "app1, app2, permitido",
    [
        ("credenciais", "credenciais", True),
        ("credenciais", "core_api", False),
        ("core_api", "credenciais", False),
        ("core_api", "core_api", None),
    ],
)
def test_relacao_entre_bancos_diferentes_e_proibida(app1, app2, permitido):
    assert router.allow_relation(modelo(app1), modelo(app2)) is permitido


@pytest.mark.parametrize(
    "banco, app_label, permitido",
    [
        ("credenciais_db", "credenciais", True),
        ("default", "credenciais", False),
        ("credenciais_db", "core_api", False),
        ("default", "core_api", None),
    ],
)
def test_migrations_ficam_no_banco_do_app(banco, app_label, permitido):
    assert router.allow_migrate(banco, app_label) is permitido
