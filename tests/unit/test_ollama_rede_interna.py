"""AKA-103: o Ollama pode ficar na própria máquina ou numa VM da rede interna.

A D1 continua valendo: nada de serviço externo, de nuvem ou fora da rede.
"""

import os

import pytest

from core_api.management.commands.testar_bge_m3_local import (
    conferir_ollama_local,
    host_da_rede_interna,
    validar_ollama_local,
)


@pytest.fixture(autouse=True)
def no_proxy_limpo(monkeypatch):
    # validar_ollama_local mexe em NO_PROXY: cada teste começa do zero e o
    # monkeypatch devolve o valor original no fim.
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")


@pytest.mark.parametrize(
    "url, host",
    [
        ("http://localhost:11434", "localhost"),
        ("http://127.0.0.1:11434", "127.0.0.1"),
        ("http://[::1]:11434", "::1"),
        ("http://192.168.15.7:11434", "192.168.15.7"),
        ("http://10.0.0.5:11434", "10.0.0.5"),
        ("http://172.16.0.9:11434", "172.16.0.9"),
        ("http://172.31.255.1:11434", "172.31.255.1"),
        ("http://[fd00::7]:11434", "fd00::7"),
        ("http://[::ffff:192.168.15.7]:11434", "::ffff:192.168.15.7"),
        ("http://LOCALHOST:11434", "localhost"),
    ],
)
def test_aceita_a_propria_maquina_e_a_rede_interna(url, host):
    assert validar_ollama_local(url) == host


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:11434",  # IP público
        "http://34.120.1.9:11434",  # nuvem
        "http://172.32.0.1:11434",  # logo depois de 172.16.0.0/12: público
        "http://169.254.169.254:11434",  # metadados da nuvem (link-local)
        "http://100.64.0.1:11434",  # CGNAT do provedor, não é a nossa rede
        "http://[2001:4860:4860::8888]:11434",  # IPv6 público
        "http://ollama.empresa.com:11434",  # nome: o DNS pode apontar para fora
        "http://ia.local:11434",
        "http://0.0.0.0:11434",
        "http://3232239367:11434",  # 192.168.15.7 escrito como número
        "http://:11434",
    ],
)
def test_recusa_o_que_nao_e_da_rede_interna(url):
    with pytest.raises(ValueError, match="nem um IP da rede interna"):
        validar_ollama_local(url)


def test_recusa_usuario_e_senha_na_url():
    with pytest.raises(ValueError, match="usuário ou senha"):
        validar_ollama_local("http://ia:segredo@192.168.15.7:11434")


def test_ip_da_vm_da_ia_fica_fora_do_proxy():
    os.environ["NO_PROXY"] = "intranet.exemplo"

    validar_ollama_local("http://192.168.15.7:11434")

    hosts = set(os.environ["NO_PROXY"].split(","))
    assert {"192.168.15.7", "localhost", "127.0.0.1", "intranet.exemplo"} <= hosts
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


@pytest.mark.parametrize("host", ["192.168.0.1", "10.255.255.255", "127.0.0.2"])
def test_host_da_rede_interna(host):
    assert host_da_rede_interna(host)


def test_teste_de_desempenho_continua_so_na_propria_maquina():
    """O testar_bge_m3_local mede a CPU e a RAM de onde roda: com o Ollama em
    outra VM, os números seriam da máquina errada."""
    with pytest.raises(SystemExit, match="não é a máquina local"):
        conferir_ollama_local("http://192.168.15.7:11434")

    assert conferir_ollama_local("http://localhost:11434") == "localhost"
