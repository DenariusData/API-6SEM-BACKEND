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

## Resultados do teste de stress

Cada VM do deploy foi recriada num container com **512 MB de RAM (sem swap)
e 1 vCPU**, rodando os mesmos processos da VM real, e recebeu carga do k6 em
degraus até quebrar. Detalhes em [stress/RELATORIO.md](stress/RELATORIO.md);
gráficos em [stress/relatorio.html](stress/relatorio.html); dados brutos em
[stress/resultados/](stress/resultados/).

### Resumo por VM

| VM | Situação com 512 MB | Gargalo | Quebrou com | Recomendação |
| --- | --- | --- | --- | --- |
| 🟢 **front** (nginx) | Sobra memória | CPU | ~300 usuários (540 req/s), sem erros | **512 MB / 1 vCPU** |
| 🟡 **back** (Django + 2 Postgres) | No limite | CPU, depois memória | ~26 usuários (42 req/s) | **1 GB / 2 vCPU** |
| 🔴 **ia** (Ollama) | **Não funciona** | Memória | 1ª pergunta (falta de memória) | **6 GB / 2+ vCPU** |
| 🟢 **monitor** (Prometheus + Grafana) | Atende a equipe | Memória | ~80 painéis abertos ao mesmo tempo | **512 MB a 1 GB** |

### Memória no pico (limite: 512 MB)

| VM | Parada | Pico | Uso do limite |
| --- | ---: | ---: | --- |
| front | 13 MB | 25 MB | `█░░░░░░░░░░░░░░░░░░░` 5% |
| back | 270 MB | 339 MB | `█████████████░░░░░░░` 66% (+~150 MB do Ubuntu na VM ≈ 95%) |
| ia | 18 MB | 512 MB | `████████████████████` **100%: processo morto 121 vezes** |
| monitor | 157 MB | 485 MB | `███████████████████░` 95% |

### Desempenho sob carga

| VM | Latência p95 (teste todo, até a carga máxima) | Erros | Mortes por falta de memória | Alerta que disparou |
| --- | ---: | ---: | ---: | --- |
| front | 1,6 s (600 usuários) | 0% | 0 | — |
| back | 3,8 s (120 usuários) | 0,18% | 0 | `ApiForaDoAr`, `ApiLenta`, `ApiMuitoLenta` |
| ia | — | **100%** | **121** | — |
| monitor | 21,7 s (200 painéis) | 0,05% | 0 | — |

### Quanto a IA precisa

**Memória decide se funciona.** Os dois modelos (bge-m3 e llama3.2) ficam
carregados juntos:

| Memória | Busca (embedding) | Resposta (chat) | Pico | Resultado |
| --- | :---: | :---: | ---: | --- |
| 512 MB | ❌ | ❌ | 512 MB | processo morto |
| 1 GB | ❌ | ❌ | 939 MB | processo morto |
| 1,5 GB | ✅ | ❌ | 1.536 MB | processo morto |
| 2 GB | ✅ | ❌ | 1.997 MB | processo morto |
| 3 GB | ✅ | ❌ | 3.020 MB | processo morto |
| **4 GB** | ✅ | ✅ | 3.547 MB | **funciona** |

Na VM real somam-se o contexto de 4096 tokens e o Ubuntu (~4,1 GB no total),
por isso a recomendação é **6 GB**.

**CPU decide a velocidade.** Resposta com o modelo já carregado, comparada à
meta do cliente (D15: menos de 10 s):

| vCPU | Tempo da resposta | Meta D15 (< 10 s) |
| --- | --- | :---: |
| 1 | `████████████████` 16,4 s | ❌ |
| 2 | `███████` 6,9 s | ✅ |
| 4 | `███` 3,3 s | ✅ |

### Por que o monitoramento numa VM própria

- **Precisa continuar no ar quando outra VM trava:** durante o stress do back,
  o `ApiForaDoAr` disparou com a CPU do back em 100%. Dentro do back, o
  Prometheus estaria disputando a mesma CPU.
- **Nenhuma VM tem memória sobrando:** o monitor usa de 157 a 485 MB; no back
  isso estoura, e na IA falta memória até para o modelo.
- **O front é a VM exposta à internet:** colocar o Grafana nela aumenta a
  superfície de ataque.
- **O custo (uma VM a mais) é coberto pelo alerta `Watchdog`**, sempre
  disparado: se ele parar de chegar, o monitoramento caiu.

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
