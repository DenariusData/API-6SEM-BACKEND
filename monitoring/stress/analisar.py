"""
Resume os resultados do executar_stress.py (e do dimensionar_ia.py) em
monitoring/stress/resultados/resumo.json: linha de base, uso sob carga,
ponto de ruptura e o recurso que satura primeiro em cada VM, mais a série
de 10 em 10 s usada nos gráficos do relatório.

    python monitoring/stress/analisar.py
"""

import csv
import gzip
import json
import statistics
from collections import defaultdict
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RES = AQUI / "resultados"
JANELA = 10  # segundos por ponto da série

# Limite de latência (p95) considerado "ainda atende" em cada VM, o mesmo dos
# thresholds dos cenários do k6.
LIMITE_P95_MS = {"front": 500, "back": 500, "ia": 45_000, "monitor": 2_000}


def pct(valores, p):
    if not valores:
        return 0.0
    v = sorted(valores)
    k = (len(v) - 1) * p / 100
    i = int(k)
    return v[i] if i + 1 >= len(v) else v[i] + (v[i + 1] - v[i]) * (k - i)


def ler_recursos(vm):
    with open(RES / vm / "recursos.csv", encoding="utf-8") as f:
        return [
            {
                **r,
                "ts": float(r["ts"]),
                "cpu_pct": float(r["cpu_pct"]),
                "mem_mb": float(r["mem_mb"]),
                "limite_mb": float(r["limite_mb"]),
            }
            for r in csv.DictReader(f)
        ]


def ler_k6(vm):
    caminho = RES / vm / "k6.csv.gz"
    if not caminho.exists():
        return []
    with gzip.open(caminho, "rt", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def serie(vm, marcos, recursos, k6):
    t0 = marcos["base_inicio"]
    baldes = defaultdict(
        lambda: {"dur": [], "reqs": 0, "falhas": 0, "vus": 0, "cpu": [], "mem": []}
    )
    for linha in k6:
        try:
            ts, valor = float(linha["timestamp"]), float(linha["metric_value"])
        except (KeyError, ValueError):
            continue
        b = baldes[int((ts - t0) // JANELA)]
        nome = linha["metric_name"]
        if nome == "http_req_duration":
            b["dur"].append(valor)
        elif nome == "http_reqs":
            b["reqs"] += 1
        elif nome == "http_req_failed" and valor == 1:
            b["falhas"] += 1
        elif nome == "vus":
            b["vus"] = max(b["vus"], valor)
    for a in recursos:
        if a["vm"] == vm and a["ts"] >= t0:
            b = baldes[int((a["ts"] - t0) // JANELA)]
            b["cpu"].append(a["cpu_pct"])
            b["mem"].append(a["mem_mb"])
    pontos = []
    for i in sorted(baldes):
        b = baldes[i]
        pontos.append(
            {
                "t": i * JANELA,
                "vus": int(b["vus"]),
                "rps": round(b["reqs"] / JANELA, 2),
                "p95_ms": round(pct(b["dur"], 95), 1),
                "erro_pct": round(100 * b["falhas"] / b["reqs"], 2) if b["reqs"] else 0,
                "cpu_pct": round(max(b["cpu"], default=0), 1),
                "mem_mb": round(max(b["mem"], default=0), 1),
            }
        )
    return pontos


def resumir(vm):
    marcos = json.loads((RES / vm / "marcos.json").read_text(encoding="utf-8"))
    recursos = ler_recursos(vm)
    k6 = ler_k6(vm)
    minhas = [a for a in recursos if a["vm"] == vm]
    base = [
        a for a in minhas if marcos["base_inicio"] <= a["ts"] < marcos["carga_inicio"]
    ]
    carga = [
        a for a in minhas if marcos["carga_inicio"] <= a["ts"] <= marcos["carga_fim"]
    ]
    pos = [a for a in minhas if marcos["carga_fim"] + 15 <= a["ts"] <= marcos["fim"]]
    limite = max((a["limite_mb"] for a in minhas), default=512)

    resumo_k6 = {}
    arq = RES / vm / "k6-resumo.json"
    if arq.exists():
        m = json.loads(arq.read_text(encoding="utf-8"))["metrics"]
        dur = m.get("http_req_duration", {})
        resumo_k6 = {
            "requisicoes": m.get("http_reqs", {}).get("count", 0),
            "req_por_s": round(m.get("http_reqs", {}).get("rate", 0), 2),
            "lat_media_ms": round(dur.get("avg", 0), 1),
            "lat_p95_ms": round(dur.get("p(95)", 0), 1),
            "lat_p99_ms": round(dur.get("p(99)", 0), 1),
            "lat_max_ms": round(dur.get("max", 0), 1),
            "erro_pct": round(100 * m.get("http_req_failed", {}).get("value", 0), 2),
            "por_cenario": {
                k.split("scenario:")[1].rstrip("}"): {
                    "p95_ms": round(v.get("p(95)", 0), 1),
                    "media_ms": round(v.get("avg", 0), 1),
                }
                for k, v in m.items()
                if k.startswith("http_req_duration{scenario:")
            },
        }

    pontos = serie(vm, marcos, recursos, k6)
    ini_carga = marcos["carga_inicio"] - marcos["base_inicio"]
    ruptura = next(
        (
            p
            for p in pontos
            if p["t"] >= ini_carga
            and p["vus"] > 0
            and (p["p95_ms"] > LIMITE_P95_MS[vm] or p["erro_pct"] > 1)
        ),
        None,
    )

    cpu_carga = [a["cpu_pct"] for a in carga]
    mem_carga = [a["mem_mb"] for a in carga]
    mem_pico = max(mem_carga + [a["mem_mb"] for a in pos], default=0)
    cpu_saturada = (
        100 * sum(c >= 95 for c in cpu_carga) / len(cpu_carga) if cpu_carga else 0
    )
    eventos = marcos.get("eventos", [])
    ooms = [e for e in eventos if e["vm"] == vm and e["evento"] == "oom"]

    # Qual recurso chegou primeiro ao limite?
    mem_frac = mem_pico / limite if limite else 0
    if ooms or mem_frac >= 0.9:
        gargalo = "memoria"
    elif cpu_saturada >= 20:
        gargalo = "cpu"
    elif mem_frac >= 0.7:
        gargalo = "memoria"
    else:
        gargalo = "cpu" if pct(cpu_carga, 95) > 80 else "folga"

    return {
        "vm": vm,
        "limite_mb": limite,
        "base": {
            "mem_mb": round(statistics.mean([a["mem_mb"] for a in base]), 1)
            if base
            else 0,
            "cpu_pct": round(statistics.mean([a["cpu_pct"] for a in base]), 1)
            if base
            else 0,
        },
        "carga": {
            "mem_p50_mb": round(pct(mem_carga, 50), 1),
            "mem_p95_mb": round(pct(mem_carga, 95), 1),
            "mem_pico_mb": round(mem_pico, 1),
            "mem_pico_pct": round(100 * mem_frac, 1),
            "cpu_media_pct": round(statistics.mean(cpu_carga), 1) if cpu_carga else 0,
            "cpu_p95_pct": round(pct(cpu_carga, 95), 1),
            "cpu_tempo_saturada_pct": round(cpu_saturada, 1),
        },
        "pos_carga_mem_mb": round(statistics.mean([a["mem_mb"] for a in pos]), 1)
        if pos
        else 0,
        "k6": resumo_k6,
        "ruptura": ruptura,
        "ooms": len(ooms),
        "eventos": eventos,
        "vm_caiu": not marcos.get("estado_final", {}).get("Running", True),
        "oom_killed": marcos.get("estado_final", {}).get("OOMKilled", False),
        "alertas": marcos.get("alertas", []),
        "gargalo": gargalo,
        "inicio_carga_s": round(ini_carga),
        "fim_carga_s": round(marcos["carga_fim"] - marcos["base_inicio"]),
        "serie": pontos,
    }


def main():
    resumo = {}
    for vm in ["front", "back", "ia", "monitor"]:
        if (RES / vm / "marcos.json").exists():
            resumo[vm] = resumir(vm)
            r = resumo[vm]
            base, carga, k6 = r["base"], r["carga"], r["k6"]
            ruptura = r["ruptura"] and (r["ruptura"]["vus"], r["ruptura"]["t"])
            print(
                f"{vm:8} base {base['mem_mb']:>6} MB"
                f" | pico {carga['mem_pico_mb']:>6} MB ({carga['mem_pico_pct']}%)"
                f" | CPU p95 {carga['cpu_p95_pct']}%"
                f" saturada {carga['cpu_tempo_saturada_pct']}%"
                f" | p95 {k6.get('lat_p95_ms')} ms erro {k6.get('erro_pct')}%"
                f" | OOM {r['ooms']} | gargalo {r['gargalo']} | ruptura {ruptura}"
            )
    dim = RES / "ia-dimensionamento.json"
    if dim.exists():
        resumo["ia_dimensionamento"] = json.loads(dim.read_text(encoding="utf-8"))
    (RES / "resumo.json").write_text(
        json.dumps(resumo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("->", RES / "resumo.json")


if __name__ == "__main__":
    main()
