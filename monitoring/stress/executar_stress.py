"""
Roda o teste de stress de cada VM do AkaVision e grava o uso de recursos.

Para cada VM (front, back, ia, monitor):
  1. reinicia a VM (memória limpa) e espera ela responder;
  2. mede 60 s parada (linha de base);
  3. roda o cenário do k6 (k6/<vm>.js) com a carga subindo em degraus;
  4. mede mais 30 s depois da carga (a memória volta ou fica presa?).

Durante tudo, a cada ~1 s, guarda CPU e memória das quatro VMs (docker
stats) e os eventos de OOM (docker events). As outras VMs continuam ligadas:
o Prometheus da VM de monitoramento coleta todas o tempo todo.

Uso (da raiz do backend, com o docker-compose.stress.yml no ar):
    python monitoring/stress/executar_stress.py todas
    python monitoring/stress/executar_stress.py back ia

Resultados em monitoring/stress/resultados/<vm>/.
"""

import csv
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
COMPOSE = ["docker", "compose", "-f", str(AQUI / "docker-compose.stress.yml")]
VMS = ["vm-front", "vm-back", "vm-ia", "vm-monitor"]
ORDEM = ["front", "back", "ia", "monitor"]
BASE_S = 60
RESFRIAR_S = 30

UNIDADES = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "kB": 1e3, "MB": 1e6, "GB": 1e9}


def em_bytes(texto):
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", texto.strip())
    return float(m.group(1)) * UNIDADES.get(m.group(2), 1) if m else 0.0


def agora():
    return datetime.now(timezone.utc).timestamp()


class Coletor:
    """docker stats em laço + docker events de OOM, em threads."""

    def __init__(self):
        self.amostras = []
        self.ooms = []
        self._parar = threading.Event()

    def _stats(self):
        fmt = "{{json .}}"
        while not self._parar.is_set():
            ts = agora()
            saida = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", fmt, *VMS],
                capture_output=True, text=True,
            ).stdout
            for linha in saida.splitlines():
                try:
                    d = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                uso, limite = (d.get("MemUsage", "0B / 0B").split("/") + ["0B"])[:2]
                self.amostras.append({
                    "ts": round(ts, 2),
                    "vm": d["Name"].replace("vm-", ""),
                    "cpu_pct": float(d["CPUPerc"].rstrip("%") or 0),
                    "mem_mb": round(em_bytes(uso) / 1024**2, 1),
                    "limite_mb": round(em_bytes(limite) / 1024**2, 1),
                    "pids": int(d.get("PIDs") or 0),
                })

    def _eventos(self):
        self._proc_ev = subprocess.Popen(
            ["docker", "events", "--filter", "event=oom", "--filter", "event=die",
             "--format", "{{json .}}"],
            stdout=subprocess.PIPE, text=True,
        )
        for linha in self._proc_ev.stdout:
            try:
                d = json.loads(linha)
            except json.JSONDecodeError:
                continue
            nome = d.get("Actor", {}).get("Attributes", {}).get("name", "")
            if nome in VMS:
                self.ooms.append({"ts": d.get("time"), "vm": nome.replace("vm-", ""),
                                  "evento": d.get("Action")})

    def iniciar(self):
        threading.Thread(target=self._stats, daemon=True).start()
        threading.Thread(target=self._eventos, daemon=True).start()

    def parar(self):
        self._parar.set()
        if getattr(self, "_proc_ev", None):
            self._proc_ev.terminate()


def esperar(url, codigos=(200, 401), limite_s=180, cabecalhos=None):
    """Espera a URL responder (de dentro da rede, via container curl)."""
    fim = time.time() + limite_s
    args = ["docker", "run", "--rm", "--network", "akavision-net", "curlimages/curl:8.11.1",
            "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "5"]
    for k, v in (cabecalhos or {}).items():
        args += ["-H", f"{k}: {v}"]
    while time.time() < fim:
        codigo = subprocess.run(args + [url], capture_output=True, text=True).stdout.strip()
        if codigo.isdigit() and int(codigo) in codigos:
            return True
        time.sleep(3)
    return False


PRONTO = {
    "front": ("http://10.10.10.6/", {}),
    "back": ("http://10.10.10.5:8000/api/auth/me/", {"Host": "10.10.10.5:8000"}),
    "ia": ("http://10.10.10.7:11434/", {}),
    "monitor": ("http://10.10.10.8:3000/api/health", {}),
}


def consultar_alertas(inicio, fim):
    """Alertas que ficaram 'firing' no Prometheus da VM de monitoramento."""
    q = urllib.request.quote('max by (alertname, severidade, vm) (ALERTS{alertstate="firing"})')
    url = f"http://10.10.10.8:9090/api/v1/query_range?query={q}&start={inicio}&end={fim}&step=15"
    saida = subprocess.run(
        ["docker", "run", "--rm", "--network", "akavision-net", "curlimages/curl:8.11.1",
         "-s", "-m", "20", url],
        capture_output=True, text=True,
    ).stdout
    try:
        dados = json.loads(saida)["data"]["result"]
    except (json.JSONDecodeError, KeyError):
        return []
    return [
        {**r["metric"], "primeiro": float(r["values"][0][0]), "ultimo": float(r["values"][-1][0])}
        for r in dados
    ]


def rodar(vm, coletor):
    pasta = AQUI / "resultados" / vm
    pasta.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {vm}: reiniciando a VM")
    subprocess.run(["docker", "restart", f"vm-{vm}"], capture_output=True)
    url, cab = PRONTO[vm]
    if not esperar(url, cabecalhos=cab):
        print(f"!!! vm-{vm} não respondeu em 3 min")
    marcos = {"vm": vm, "reiniciada": agora()}

    print(f"=== {vm}: linha de base ({BASE_S} s parada)")
    marcos["base_inicio"] = agora()
    time.sleep(BASE_S)
    marcos["carga_inicio"] = agora()

    print(f"=== {vm}: carga (k6/{vm}.js)")
    k6 = subprocess.run(
        COMPOSE + ["--profile", "carga", "run", "--rm", "k6", "run", "--quiet",
                   "--summary-export", f"/resultados/{vm}/k6-resumo.json",
                   "--out", f"csv=/resultados/{vm}/k6.csv.gz",
                   f"/scripts/{vm}.js"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    marcos["carga_fim"] = agora()
    (pasta / "k6-saida.txt").write_text(k6.stdout + "\n" + k6.stderr, encoding="utf-8")
    print(k6.stdout[-2500:])

    print(f"=== {vm}: resfriando ({RESFRIAR_S} s)")
    time.sleep(RESFRIAR_S)
    marcos["fim"] = agora()

    estado = subprocess.run(
        ["docker", "inspect", "-f", "{{json .State}}", f"vm-{vm}"],
        capture_output=True, text=True,
    ).stdout
    marcos["estado_final"] = json.loads(estado or "{}")
    marcos["alertas"] = consultar_alertas(marcos["base_inicio"], marcos["fim"])
    log = subprocess.run(["docker", "logs", "--since", "15m", f"vm-{vm}"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    (pasta / "vm-log.txt").write_text((log.stdout + log.stderr)[-200_000:], encoding="utf-8")

    amostras = [a for a in coletor.amostras if marcos["reiniciada"] <= a["ts"] <= marcos["fim"]]
    with open(pasta / "recursos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "vm", "cpu_pct", "mem_mb", "limite_mb", "pids"])
        w.writeheader()
        w.writerows(amostras)
    marcos["eventos"] = [e for e in coletor.ooms
                         if marcos["reiniciada"] - 5 <= (e["ts"] or 0) <= marcos["fim"] + 5]
    (pasta / "marcos.json").write_text(json.dumps(marcos, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    # Se a VM morreu no teste, sobe de novo para não atrapalhar a próxima.
    if not marcos["estado_final"].get("Running", True):
        subprocess.run(["docker", "start", f"vm-{vm}"], capture_output=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pedidas = sys.argv[1:] or ["todas"]
    vms = ORDEM if "todas" in pedidas else [v for v in ORDEM if v in pedidas]
    coletor = Coletor()
    coletor.iniciar()
    try:
        for vm in vms:
            rodar(vm, coletor)
    finally:
        coletor.parar()
    print("\nResultados em", AQUI / "resultados")


if __name__ == "__main__":
    main()
