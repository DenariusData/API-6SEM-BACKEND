"""
Gera tráfego na API local para ver o painel do Grafana se mexer e os
alertas dispararem. Só para a máquina de desenvolvimento.

    python monitoring/simular_carga.py normal          # navegação comum
    python monitoring/simular_carga.py login-falho     # dispara MuitasFalhasDeLogin
    python monitoring/simular_carga.py perguntas -n 6  # perguntas simultâneas à IA

O usuário vem de AKV_EMAIL/AKV_SENHA ou, sem elas, do usuário de demonstração
do `seed_usuarios_demo` (rode `python manage.py seed_usuarios_demo` antes).
"""

import argparse
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

API = os.getenv("AKV_API", "http://127.0.0.1:8000/api")
EMAIL = os.getenv("AKV_EMAIL", "engenharia@akaer.com.br")
SENHA = os.getenv("AKV_SENHA", "eng123")

ROTAS_COMUNS = [
    "/auth/me/",
    "/tipos-documento/",
    "/areas/",
    "/niveis-sigilo/",
    "/idiomas/",
    "/documents/meus/",
]
PERGUNTAS = [
    "Como é feito o ensaio de tração em materiais compósitos?",
    "Quais são os requisitos de pressurização da cabine?",
    "Qual o procedimento de inspeção de soldas?",
    "Quais normas tratam de tolerância dimensional?",
]


def entrar():
    r = requests.post(
        f"{API}/auth/login/", json={"email": EMAIL, "password": SENHA}, timeout=10
    )
    r.raise_for_status()
    cab = {"Authorization": f"Bearer {r.json()['token']}"}
    # Desde o AKA-109 as telas exigem o aceite dos termos obrigatórios.
    if r.json().get("termos_pendentes"):
        termos = requests.get(f"{API}/auth/termos-pendentes/", headers=cab, timeout=10)
        respostas = [{"termo_id": t["id"], "aceito": True} for t in termos.json()["termos"]]
        requests.post(f"{API}/auth/termos/aceite/", json={"respostas": respostas},
                      headers=cab, timeout=10)
    return cab


def normal(total):
    cab = entrar()
    for i in range(total):
        rota = random.choice(ROTAS_COMUNS)
        r = requests.get(f"{API}{rota}", headers=cab, timeout=10)
        # De vez em quando um acesso sem token e uma rota inexistente.
        if i % 10 == 0:
            requests.get(f"{API}/auth/me/", timeout=10)
            requests.get(f"{API}/nao-existe/", timeout=10)
        print(f"{r.status_code} {rota}")
        time.sleep(0.2)


def login_falho(total):
    # E-mail que não existe: não bloqueia a conta de ninguém.
    for _ in range(total):
        r = requests.post(
            f"{API}/auth/login/",
            json={"email": "nao-existe@exemplo.invalid", "password": "errada"},
            timeout=10,
        )
        print(r.status_code, end=" ", flush=True)
    print()


def perguntar(cab, texto):
    inicio = time.perf_counter()
    r = requests.post(
        f"{API}/perguntas/", json={"pergunta": texto}, headers=cab, timeout=650
    )
    return r.status_code, time.perf_counter() - inicio


def perguntas(total, simultaneas):
    cab = entrar()
    with ThreadPoolExecutor(simultaneas) as pool:
        tarefas = [
            pool.submit(perguntar, cab, random.choice(PERGUNTAS)) for _ in range(total)
        ]
        for t in tarefas:
            status, segundos = t.result()
            print(f"{status} em {segundos:.1f} s")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cenario", choices=["normal", "login-falho", "perguntas"])
    ap.add_argument(
        "-t", "--total", type=int, default=None, help="quantidade de requisições"
    )
    ap.add_argument(
        "-n", "--simultaneas", type=int, default=3, help="perguntas ao mesmo tempo"
    )
    args = ap.parse_args()
    if args.cenario == "normal":
        normal(args.total or 200)
    elif args.cenario == "login-falho":
        login_falho(args.total or 40)
    else:
        perguntas(args.total or args.simultaneas, args.simultaneas)


if __name__ == "__main__":
    main()
