# Monitoramento local

Prometheus, Grafana e Alertmanager rodando em Docker na máquina de
desenvolvimento, observando o Django, o front (Vite), o Ollama e os dois
Postgres.

```
               ┌──────────── Docker ────────────────────────────────┐
Django :8000 ◄─┤ Prometheus :9090 ── regras ──► Alertmanager :9093  │
Vite   :5173 ◄─┤   ▲  ▲  ▲                                           │
Ollama :11434◄─┤   │  │  └ blackbox (sondas "está no ar?")           │
               │   │  └── node-exporter (CPU, RAM, disco)            │
Postgres ◄─────┤   └───── postgres-exporter (rag e credenciais)      │
               │ Grafana :3000 ── lê Prometheus e Alertmanager       │
               └─────────────────────────────────────────────────────┘
```

## Subir

Pré-requisitos: Docker Desktop aberto e `pip install -r requirements.txt`
(traz o `django-prometheus`).

1. No `.env` do backend, libere o host que o Prometheus usa para chegar no
   Django:

   ```
   ALLOWED_HOSTS=localhost,127.0.0.1,host.docker.internal
   ```

2. Suba os bancos, o backend, o front e o Ollama como de costume.
3. Suba o monitoramento:

   ```bash
   docker compose -f monitoring/docker-compose.monitoring.yml up -d
   ```

| O quê | Endereço |
| --- | --- |
| Painel do Grafana | http://localhost:3000 (abre sem login; `admin`/`admin` para editar) |
| Prometheus (alvos, consultas, regras) | http://localhost:9090/targets |
| Alertmanager | http://localhost:9093 |
| Métricas do Django | http://localhost:8000/metrics |

Para parar: `docker compose -f monitoring/docker-compose.monitoring.yml down`
(acrescente `-v` para apagar o histórico).

## O que é medido

| Camada | Métricas | Origem |
| --- | --- | --- |
| Disponibilidade | API, Ollama e front respondendo; bancos no ar | blackbox, postgres-exporter |
| API (RED) | requisições/min, % de 5xx, latência p95 por rota | `django-prometheus` |
| IA | tempo de cada chamada ao Ollama (embed/chat), chamadas em andamento, sucesso/erro/timeout | `core_api/utils/metricas.py` |
| Segurança e LGPD | falhas de login, acessos negados, exportações de dados pessoais | `django-prometheus` por rota |
| Infraestrutura | CPU, memória, disco, conexões e tamanho dos bancos | node-exporter, postgres-exporter |

Nenhuma métrica tem usuário, e-mail ou texto da pergunta como rótulo (LGPD).
A rota `/metrics` fica fora de `/api/`, então o nginx da VM não a publica.

## Alertas e cenário

As regras estão em `prometheus/alertas.yml`, com os limites do cenário
hipotético (40 analistas, ~400 perguntas/dia, pico de ~30 req/min e 6
perguntas simultâneas, IA em CPU):

| SLO | Meta |
| --- | --- |
| Disponibilidade da API | 99,5% em horário comercial |
| Latência p95 das rotas comuns | < 500 ms |
| p95 da resposta da IA | < 10 s (decisão D15 do cliente) |
| Erros 5xx | < 1% |

Por padrão os alertas aparecem no Alertmanager e no painel. Para receber no
Discord/Telegram/e-mail, configure um receiver em `alertmanager/alertmanager.yml`.

## Simular carga

```bash
python monitoring/simular_carga.py normal              # navegação comum
python monitoring/simular_carga.py login-falho -t 40   # dispara MuitasFalhasDeLogin
python monitoring/simular_carga.py perguntas -n 6 -t 12  # 6 perguntas simultâneas à IA
```

Usa o usuário de `python manage.py seed_usuarios_demo` (ou `AKV_EMAIL` e
`AKV_SENHA`).

## Limitações do ambiente local

- No Windows, o node-exporter mede a máquina Linux do Docker Desktop (WSL).
  O disco `/mnt/host/c` é o C: do Windows.
- O cAdvisor (recursos por container) não funciona com o image store do
  containerd do Docker Desktop, então ficou de fora. Na VM Ubuntu ele funciona.
- Com o gunicorn de várias workers (VM), o `prometheus_client` precisa do modo
  multiprocesso (`PROMETHEUS_MULTIPROC_DIR`). O `runserver` local é um
  processo só e não precisa disso.
- O Ollama não tem `/metrics`: ele é medido por fora (sonda) e pelo Django
  (tempo de cada chamada).
