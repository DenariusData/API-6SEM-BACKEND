"""
Métricas próprias do AkaVision para o Prometheus (monitoramento local).

As métricas HTTP gerais (requisições, latência e status por view) vêm do
django-prometheus. Aqui ficam só as que ele não enxerga: as chamadas ao
Ollama, que não tem um /metrics próprio.

LGPD: nenhum rótulo leva usuário, e-mail ou texto da pergunta, só a
operação (embed/chat) e o resultado.
"""

import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

# Faixas pensadas para IA em CPU: embedding em ~1 s, resposta em 15-45 s,
# timeout do gunicorn em 620 s.
FAIXAS_IA = (0.5, 1, 2.5, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600)

OLLAMA_DURACAO = Histogram(
    "akavision_ollama_duracao_segundos",
    "Tempo de cada chamada ao Ollama",
    ["operacao"],
    buckets=FAIXAS_IA,
)
OLLAMA_CHAMADAS = Counter(
    "akavision_ollama_chamadas_total",
    "Chamadas ao Ollama por resultado",
    ["operacao", "resultado"],
)
OLLAMA_EM_ANDAMENTO = Gauge(
    "akavision_ollama_em_andamento",
    "Chamadas ao Ollama acontecendo agora (fila da IA)",
)


@contextmanager
def medir_ollama(operacao):
    """Mede uma chamada ao Ollama. `operacao` é o fim do caminho (embed, chat).

    O bloco marca `resultado` como "erro" se levantar exceção; o chamador
    pode trocar para "timeout" antes de levantar.
    """
    estado = {"resultado": "sucesso"}
    OLLAMA_EM_ANDAMENTO.inc()
    inicio = time.perf_counter()
    try:
        yield estado
    except Exception:
        if estado["resultado"] == "sucesso":
            estado["resultado"] = "erro"
        raise
    finally:
        OLLAMA_EM_ANDAMENTO.dec()
        OLLAMA_DURACAO.labels(operacao).observe(time.perf_counter() - inicio)
        OLLAMA_CHAMADAS.labels(operacao, estado["resultado"]).inc()
