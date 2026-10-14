"""
AKA-95: testa se o bge-m3 roda na máquina local, sem chamada externa (D1).

O que o script faz:
    1. Confere que o Ollama é local (localhost) e que o bge-m3 está baixado.
    2. Gera embeddings de uma amostra de trechos (padrão: 20), um por vez,
       anotando o tempo de cada um. Depois roda a mesma amostra em lote.
    3. Anota a memória usada e se o modelo rodou em CPU ou GPU (via /api/ps do Ollama).
    4. Se receber o .npz do Colab, compara: mesma dimensão e similaridade alta
       para os mesmos trechos (mínimo 0.98, o mesmo critério do importar_embeddings.py).
    5. Grava um resumo em Markdown pronto para colar na wiki.

Pré-requisitos:
    ollama pull bge-m3
    python -m pip install requests numpy psutil     (psutil é opcional: mede a RAM do Ollama)

Uso (na mesma pasta do gerar_embeddings.py):
    python testar_bge_m3_local.py --colab embeddings_bge_m3.npz
    python testar_bge_m3_local.py                     sem comparar com o Colab
    python testar_bge_m3_local.py --amostra 50 --chunks caminho/chunks.jsonl

Não usa o PostgreSQL e não grava nada no banco.
"""

import argparse
import json
import platform
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import requests
from gerar_embeddings import ARQUIVO_CHUNKS, OLLAMA_URL, ErroOllama, gerar_vetores

SIMILARIDADE_MINIMA = 0.98
HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

try:
    import psutil
except ImportError:
    psutil = None


# ---------- Máquina ----------


def info_maquina():
    info = {
        "sistema": f"{platform.system()} {platform.release()}",
        "cpu": platform.processor() or platform.machine(),
        "nucleos": None,
        "ram_total_gb": None,
        "gpu": "não detectada (nvidia-smi ausente)",
    }
    if psutil:
        info["nucleos"] = psutil.cpu_count(logical=True)
        info["ram_total_gb"] = round(psutil.virtual_memory().total / 1024**3, 1)
    if shutil.which("nvidia-smi"):
        try:
            saida = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            if saida:
                info["gpu"] = saida.replace("\n", " | ")
        except (OSError, subprocess.SubprocessError):
            pass
    return info


def vram_em_uso_mb():
    """VRAM usada na GPU 0 (só NVIDIA). None se não der para medir."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        saida = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return int(saida.splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class MedidorRam:
    """Amostra, em segundo plano, a RAM somada dos processos do Ollama e guarda o pico."""

    def __init__(self, intervalo=0.2):
        self.intervalo = intervalo
        self.pico_mb = None
        self._parar = threading.Event()
        self._thread = None

    @staticmethod
    def ram_ollama_mb():
        total = 0
        for p in psutil.process_iter(["name", "memory_info"]):
            nome = (p.info.get("name") or "").lower()
            if "ollama" in nome and p.info.get("memory_info"):
                total += p.info["memory_info"].rss
        return total / 1024**2

    def _loop(self):
        while not self._parar.is_set():
            try:
                atual = self.ram_ollama_mb()
                self.pico_mb = max(self.pico_mb or 0, atual)
            except Exception:  # processo pode sumir no meio da leitura
                pass
            self._parar.wait(self.intervalo)

    def __enter__(self):
        if psutil:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        if self._thread:
            self._thread.join()


# ---------- Ollama ----------


def conferir_ollama_local():
    host = (urlparse(OLLAMA_URL).hostname or "").lower()
    if host not in HOSTS_LOCAIS:
        sys.exit(
            f"OLLAMA_URL aponta para '{host}', que não é a máquina local.\n"
            "A D1 exige que o embedding rode sem chamada externa. "
            "Use OLLAMA_URL=http://localhost:11434"
        )
    return host


def descarregar_modelo(modelo):
    """Tira o modelo da memória para medir o tempo de carga a frio."""
    try:
        requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": modelo, "input": [], "keep_alive": 0},
            timeout=60,
        )
        time.sleep(1)
    except requests.RequestException:
        pass


def estado_modelo(modelo):
    """Lê o /api/ps: memória total do modelo e quanto dela está na GPU."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=10)
        r.raise_for_status()
    except requests.RequestException:
        return None
    for m in r.json().get("models", []):
        nome = m.get("name", "")
        if nome == modelo or nome.split(":")[0] == modelo.split(":")[0]:
            tamanho = m.get("size", 0)
            vram = m.get("size_vram", 0)
            pct_gpu = round(100 * vram / tamanho) if tamanho else 0
            if pct_gpu >= 99:
                processador = "GPU (100%)"
            elif pct_gpu <= 0:
                processador = "CPU (100%)"
            else:
                processador = f"Híbrido ({100 - pct_gpu}% CPU / {pct_gpu}% GPU)"
            detalhes = m.get("details", {})
            return {
                "nome": nome,
                "memoria_modelo_mb": round(tamanho / 1024**2),
                "memoria_gpu_mb": round(vram / 1024**2),
                "processador": processador,
                "quantizacao": detalhes.get("quantization_level"),
                "parametros": detalhes.get("parameter_size"),
            }
    return None


def versao_ollama():
    try:
        return (
            requests.get(f"{OLLAMA_URL}/api/version", timeout=10).json().get("version")
        )
    except (requests.RequestException, ValueError):
        return None


# ---------- Amostra e Colab ----------


def carregar_chunks(caminho):
    if not caminho.exists():
        sys.exit(f"Arquivo {caminho} não encontrado. Rode antes o gerar_chunks.py")
    with caminho.open(encoding="utf-8") as f:
        return {c["id"]: c for c in (json.loads(linha) for linha in f if linha.strip())}


def carregar_colab(caminho):
    dados = np.load(caminho)
    ids = [str(i) for i in dados["ids"]]
    vetores = dados["vetores"].astype(np.float32)
    if len(ids) != len(vetores):
        sys.exit(
            "Arquivo do Colab inválido: quantidade de ids diferente da de vetores."
        )
    return dict(zip(ids, vetores))


def cosseno(a, b):
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


# ---------- Relatório ----------


def fmt(v, sufixo=""):
    return "n/d" if v is None else f"{v}{sufixo}"


def montar_markdown(r):
    m, e = r["maquina"], r["estado"] or {}
    linhas = [
        f"# AKA-95: bge-m3 na máquina local ({r['data']})",
        "",
        f"**Resultado:** {r['veredito']}",
        "",
        "## Máquina",
        "",
        "| Item | Valor |",
        "|---|---|",
        f"| Responsável pelo teste | {r['responsavel'] or 'n/d'} |",
        f"| Sistema | {m['sistema']} |",
        f"| CPU | {m['cpu']} ({fmt(m['nucleos'])} núcleos lógicos) |",
        f"| RAM total | {fmt(m['ram_total_gb'], ' GB')} |",
        f"| GPU | {m['gpu']} |",
        f"| Ollama | {fmt(r['versao_ollama'])} em `{r['ollama_url']}` (local, sem chamada externa) |",
        f"| Modelo | {e.get('nome', r['modelo'])} ({fmt(e.get('parametros'))}, {fmt(e.get('quantizacao'))}) |",
        "",
        "## Desempenho",
        "",
        "| Medida | Valor |",
        "|---|---|",
        f"| Amostra | {r['amostra']} trechos ({r['caracteres_medio']} caracteres em média) |",
        f"| Carga do modelo (1ª chamada, a frio) | {r['carga_s']:.1f} s |",
        f"| Tempo por trecho (um por vez) | média {r['media_ms']:.0f} ms, mediana {r['mediana_ms']:.0f} ms, mín. {r['min_ms']:.0f} ms, máx. {r['max_ms']:.0f} ms |",
        f"| Lote de {r['amostra']} trechos | {r['lote_s']:.2f} s ({r['lote_ms_por_trecho']:.0f} ms por trecho, {r['chunks_por_s']:.1f} trechos/s) |",
        f"| CPU ou GPU | {e.get('processador', 'n/d')} |",
        f"| Memória do modelo (Ollama) | {fmt(e.get('memoria_modelo_mb'), ' MB')} (na GPU: {fmt(e.get('memoria_gpu_mb'), ' MB')}) |",
        f"| Pico de RAM dos processos do Ollama | {fmt(r['pico_ram_mb'], ' MB')} |",
        f"| VRAM em uso na GPU (antes → depois) | {fmt(r['vram_antes_mb'], ' MB')} → {fmt(r['vram_depois_mb'], ' MB')} |",
        f"| Estimativa para um documento de 300 trechos | ~{r['estimativa_300_min']:.1f} min |",
        "",
        "## Compatibilidade com os embeddings do Colab",
        "",
    ]
    c = r["colab"]
    if c is None:
        linhas.append("Não comparado (rodado sem `--colab`).")
    else:
        linhas += [
            "| Medida | Valor |",
            "|---|---|",
            f"| Dimensão local / Colab | {r['dimensao']} / {c['dimensao']} |",
            f"| Trechos comparados | {c['comparados']} |",
            f"| Similaridade de cosseno | mín. {c['min']:.4f}, média {c['media']:.4f} |",
            f"| Critério | mín. ≥ {SIMILARIDADE_MINIMA} e mesma dimensão |",
            f"| Compatível | {'Sim' if c['ok'] else 'Não'} |",
        ]
    linhas += ["", "## Critérios de aceite", ""]
    for texto, ok in r["criterios"]:
        linhas.append(f"- [{'x' if ok else ' '}] {texto}")
    if r["falhas"]:
        linhas += ["", "## Trechos com erro", ""]
        linhas += [f"- `{i}`: {erro}" for i, erro in r["falhas"]]
    linhas += [
        "",
        "## Detalhe por trecho",
        "",
        "| Trecho | Caracteres | Tempo (ms) | Similaridade com Colab |",
        "|---|---|---|---|",
    ]
    for d in r["detalhe"]:
        sim = "n/d" if d["sim"] is None else f"{d['sim']:.4f}"
        linhas.append(f"| `{d['id']}` | {d['chars']} | {d['ms']:.0f} | {sim} |")
    linhas.append("")
    return "\n".join(linhas)


# ---------- Principal ----------


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="AKA-95: testa o bge-m3 no Ollama local.")
    ap.add_argument("--modelo", default="bge-m3")
    ap.add_argument("--amostra", type=int, default=20, help="quantos trechos testar")
    ap.add_argument("--chunks", type=Path, default=ARQUIVO_CHUNKS)
    ap.add_argument("--colab", type=Path, help="embeddings_bge_m3.npz gerado no Colab")
    ap.add_argument(
        "--semente", type=int, default=42, help="para sortear a mesma amostra"
    )
    ap.add_argument(
        "--responsavel", default="", help="nome de quem rodou (vai para a wiki)"
    )
    ap.add_argument("--saida", type=Path, default=Path("resultado_bge_m3_local.md"))
    args = ap.parse_args()

    conferir_ollama_local()
    chunks = carregar_chunks(args.chunks)
    colab = carregar_colab(args.colab) if args.colab else None

    # Amostra: se houver Colab, só trechos que existem nos dois lados
    candidatos = sorted(set(chunks) & set(colab)) if colab else sorted(chunks)
    if not candidatos:
        sys.exit("Nenhum trecho em comum entre o chunks.jsonl e o arquivo do Colab.")
    random.seed(args.semente)
    ids = random.sample(candidatos, min(args.amostra, len(candidatos)))
    textos = [chunks[i]["texto"] for i in ids]
    print(f"Amostra: {len(ids)} trechos de {len(candidatos)} disponíveis\n")

    maquina = info_maquina()
    vram_antes = vram_em_uso_mb()

    # 1. Carga a frio
    descarregar_modelo(args.modelo)
    inicio = time.perf_counter()
    try:
        dimensao = len(gerar_vetores(["teste"], args.modelo)[0])
    except ErroOllama as e:
        sys.exit(f"O Ollama respondeu com erro: {e}\nConfira com: ollama list")
    carga_s = time.perf_counter() - inicio
    print(f"Modelo carregado em {carga_s:.1f}s | vetores de {dimensao} dimensões")

    # 2. Um trecho por vez
    detalhe, falhas, locais = [], [], {}
    with MedidorRam() as medidor:
        for n, (i, texto) in enumerate(zip(ids, textos), 1):
            t0 = time.perf_counter()
            try:
                vetor = gerar_vetores([texto], args.modelo)[0]
            except ErroOllama as e:
                falhas.append((i, str(e)[:120]))
                print(f"  {n:>2}/{len(ids)} {i}: ERRO {e}")
                continue
            ms = (time.perf_counter() - t0) * 1000
            locais[i] = vetor
            sim = cosseno(vetor, colab[i]) if colab else None
            detalhe.append({"id": i, "chars": len(texto), "ms": ms, "sim": sim})
            extra = f" | sim. Colab {sim:.4f}" if sim is not None else ""
            print(f"  {n:>2}/{len(ids)} {ms:7.0f} ms{extra}")

        estado = estado_modelo(args.modelo)

        # 3. Mesma amostra em lote (como o gerar_embeddings.py faz)
        t0 = time.perf_counter()
        try:
            gerar_vetores(textos, args.modelo)
            lote_s = time.perf_counter() - t0
        except ErroOllama:
            lote_s = float("nan")

    vram_depois = vram_em_uso_mb()
    if not detalhe:
        sys.exit("Nenhum trecho gerou embedding. Veja os erros acima.")

    tempos = [d["ms"] for d in detalhe]
    lote_ms = lote_s * 1000 / len(ids)
    chunks_por_s = len(ids) / lote_s if lote_s else 0

    # 4. Comparação com o Colab
    resumo_colab = None
    if colab:
        sims = [d["sim"] for d in detalhe]
        dim_colab = len(next(iter(colab.values())))
        resumo_colab = {
            "dimensao": dim_colab,
            "comparados": len(sims),
            "min": min(sims),
            "media": statistics.mean(sims),
            "ok": dim_colab == dimensao and min(sims) >= SIMILARIDADE_MINIMA,
        }

    gerou_local = not falhas and estado is not None
    criterios = [
        ("bge-m3 gera embeddings localmente sem chamada externa", gerou_local),
        ("Tempo e memória anotados", estado is not None),
        (
            "Embeddings locais compatíveis com os do Colab",
            bool(resumo_colab and resumo_colab["ok"]),
        ),
    ]
    if all(ok for _, ok in criterios):
        veredito = "✅ RODA. O bge-m3 local pode ser usado na AKA-62."
    elif gerou_local and resumo_colab is None:
        veredito = "⚠️ RODA, mas falta comparar com o Colab (rode de novo com --colab)."
    elif gerou_local:
        veredito = "⚠️ RODA, mas os vetores NÃO batem com os do Colab. Investigar antes da AKA-62."
    else:
        veredito = "❌ NÃO RODOU. Levantar alternativa (modelo menor ou quantizado) antes de 19/10."

    resultado = {
        "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "responsavel": args.responsavel,
        "maquina": maquina,
        "versao_ollama": versao_ollama(),
        "ollama_url": OLLAMA_URL,
        "modelo": args.modelo,
        "estado": estado,
        "amostra": len(ids),
        "caracteres_medio": round(statistics.mean(len(t) for t in textos)),
        "carga_s": carga_s,
        "media_ms": statistics.mean(tempos),
        "mediana_ms": statistics.median(tempos),
        "min_ms": min(tempos),
        "max_ms": max(tempos),
        "lote_s": lote_s,
        "lote_ms_por_trecho": lote_ms,
        "chunks_por_s": chunks_por_s,
        "estimativa_300_min": 300 * lote_ms / 1000 / 60,
        "pico_ram_mb": round(medidor.pico_mb) if medidor.pico_mb else None,
        "vram_antes_mb": vram_antes,
        "vram_depois_mb": vram_depois,
        "dimensao": dimensao,
        "colab": resumo_colab,
        "criterios": criterios,
        "veredito": veredito,
        "falhas": falhas,
        "detalhe": detalhe,
    }

    args.saida.write_text(montar_markdown(resultado), encoding="utf-8")

    e = estado or {}
    print(f"\n{veredito}")
    print(f"  CPU/GPU:            {e.get('processador', 'n/d')}")
    print(f"  Memória do modelo:  {fmt(e.get('memoria_modelo_mb'), ' MB')}")
    print(
        f"  Tempo por trecho:   média {resultado['media_ms']:.0f} ms (lote: {lote_ms:.0f} ms)"
    )
    if resumo_colab:
        print(
            f"  Colab:              dimensão {resumo_colab['dimensao']}/{dimensao}, "
            f"similaridade mín. {resumo_colab['min']:.4f}"
        )
    print(f"\nResumo para a wiki salvo em: {args.saida.resolve()}")


if __name__ == "__main__":
    main()
