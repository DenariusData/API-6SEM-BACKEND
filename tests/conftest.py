"""Configuração comum dos testes.

O bloqueio por termos obrigatórios (AKA-109) fica desligado por padrão: os
testes de outras funcionalidades criam usuários que nunca aceitaram termos.
Os testes de termos ligam o bloqueio com override_settings.
"""

import pytest


@pytest.fixture(autouse=True)
def _sem_bloqueio_por_termos(settings):
    settings.TERMOS_EXIGIR_ACEITE = False
