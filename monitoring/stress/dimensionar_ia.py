"""
Dimensionamento da VM da IA: com 512 MB o Ollama não carrega os modelos,
então aqui a VM é recriada com mais memória (e depois mais CPU) até achar o
mínimo que funciona e o quanto cada recurso muda o tempo de resposta.

Para cada configuração: 2 embeddings (bge-m3) e 2 respostas (llama3.2), uma
de cada vez. A primeira de cada modelo inclui carregar o modelo na RAM.

Uso (da raiz do backend, com o laboratório no ar):
    python monitoring/stress/dimensionar_ia.py

Resultado em monitoring/stress/resultados/ia-dimensionamento.json. No fim a
VM da IA volta para 512 MB / 1 vCPU.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from executar_stress import COMPOSE, Coletor, esperar

AQUI = Path(__file__).resolve().parent

MEMORIAS = ["512m", "1g", "1536m", "2g", "3g", "4g"]
CPUS = ["1", "2", "4"]
MEMORIA_TESTE_CPU = "4g"

PERGUNTA = "Qual o intervalo de inspeção do trem de pouso descrito no manual?"
TRECHO = ("A inspeção do trem de pouso deve ser feita a cada 500 horas de voo ou 6 meses, "
          "o que ocorrer primeiro.")
EMBED = {"model": "bge-m3", "input": PERGUNTA}
CHAT = {
    "model": "llama3.2", "stream": False,
    "options": {"num_predict": 96, "num_ctx": 2048, "temperature": 0},
    "messages": [
        {"role": "system", "content": "Responda em português, em até 3 frases, só com base no trecho."},
        {"role": "user", "content": f"Trecho: {TRECHO}\n\nPergunta: {PERGUNTA}"},
    ],
}


def chamar(caminho, corpo):
    inicio = time.time()
    r = subprocess.run(
        ["docker", "run", "--rm", "--network", "akavision-net", "curlimages/curl:8.11.1",
         "-s", "-m", "900", "-w", "\n%{http_code}", "-H", "Content-Type: application/json",
         "-d", json.dumps(corpo), f"http://10.10.10.7:11434{caminho}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    duracao = time.time() - inicio
    corpo_resp, _, codigo = r.stdout.rpartition("\n")
    item = {"status": int(codigo) if codigo.isdigit() else 0, "segundos": round(duracao, 1)}
    try:
        d = json.loads(corpo_resp)
    except json.JSONDecodeError:
        item["erro"] = corpo_resp[:300] or r.stderr[:300]
        return item
    if "error" in d:
        item["erro"] = d["error"][:300]
    for k in ("load_duration", "prompt_eval_duration", "eval_duration", "total_duration"):
        if k in d:
            item[k.replace("_duration", "_s")] = round(d[k] / 1e9, 2)
    if d.get("eval_count") and d.get("eval_duration"):
        item["tokens_por_s"] = round(d["eval_count"] / (d["eval_duration"] / 1e9), 2)
    return item


def recriar(memoria, cpus):
    env = {**os.environ, "VM_MEMORIA": memoria, "VM_CPUS": cpus,
           "OLLAMA_MODELOS": os.environ.get("OLLAMA_MODELOS",
                                            str(Path.home() / ".ollama" / "models"))}
    subprocess.run(COMPOSE + ["up", "-d", "--force-recreate", "vm-ia"],
                   env=env, capture_output=True)
    esperar("http://10.10.10.7:11434/", codigos=(200,))


def medir(memoria, cpus, coletor):
    print(f"--- IA com {memoria} / {cpus} vCPU")
    recriar(memoria, cpus)
    inicio = time.time()
    chamadas = {"embed": [chamar("/api/embed", EMBED) for _ in range(2)],
                "chat": [chamar("/api/chat", CHAT) for _ in range(2)]}
    fim = time.time()
    estado = json.loads(subprocess.run(["docker", "inspect", "-f", "{{json .State}}", "vm-ia"],
                                       capture_output=True, text=True).stdout or "{}")
    mem = [a["mem_mb"] for a in coletor.amostras if a["vm"] == "ia" and inicio <= a["ts"] <= fim]
    cpu = [a["cpu_pct"] for a in coletor.amostras if a["vm"] == "ia" and inicio <= a["ts"] <= fim]
    log = subprocess.run(["docker", "logs", "--since", f"{int(fim - inicio) + 60}s", "vm-ia"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    avisos = [linha[-300:] for linha in (log.stdout + log.stderr).splitlines()
              if any(p in linha.lower() for p in ("memory", "oom", "killed", "error"))][-8:]
    resultado = {
        "memoria": memoria, "cpus": cpus, "chamadas": chamadas,
        "mem_pico_mb": max(mem, default=0), "cpu_pico_pct": max(cpu, default=0),
        "oom_killed": estado.get("OOMKilled"), "rodando": estado.get("Running"),
        "avisos_log": avisos,
        "funcionou": all(c["status"] == 200 for lista in chamadas.values() for c in lista),
    }
    print(json.dumps({k: v for k, v in resultado.items() if k != "avisos_log"},
                     ensure_ascii=False)[:600])
    return resultado


def main():
    coletor = Coletor()
    coletor.iniciar()
    resultados = {"memoria": [], "cpu": []}
    try:
        for m in MEMORIAS:
            resultados["memoria"].append(medir(m, "1", coletor))
        for c in CPUS:
            resultados["cpu"].append(medir(MEMORIA_TESTE_CPU, c, coletor))
    finally:
        coletor.parar()
        recriar("512m", "1")
    saida = AQUI / "resultados" / "ia-dimensionamento.json"
    saida.write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Resultado em", saida)


if __name__ == "__main__":
    main()
