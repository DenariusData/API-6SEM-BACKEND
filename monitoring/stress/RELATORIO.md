# Relatório: stress por VM, recursos e alertas

Teste feito em 27/11/2026 com o laboratório de `monitoring/stress/`.
Cada VM do AkaVision foi simulada por um container com os mesmos processos da
VM real, **limitado a 512 MB de RAM (sem swap) e 1 vCPU**, e recebeu carga
crescente (k6) até quebrar. Os dados brutos estão em `resultados/`.

## 1. Resposta curta

| VM | O que roda | Do que mais precisa | Com 512 MB / 1 vCPU | Recomendação |
| --- | --- | --- | --- | --- |
| **front** | nginx + build do Vue | **CPU** | Aguenta: usou no máximo 25 MB (5%). Satura a CPU em ~550 req/s; p95 > 500 ms a partir de ~300 usuários simultâneos | 512 MB sobra; 1 vCPU basta para o cenário (40 analistas) |
| **back** | gunicorn 3x4 + Postgres rag + Postgres credenciais | **CPU** (login e consultas) e, em seguida, **memória** | Funciona, mas no limite: 270 MB parado, 338 MB no pico (66%) **sem contar o Ubuntu**. CPU em 100% com ~26 usuários; login com fila a partir de 3/s | 1 GB e 2 vCPU; baixar o `shared_buffers` de 512 MB |
| **ia** | Ollama (bge-m3 + llama3.2) | **Memória** para funcionar, **CPU** para ser rápida | **Não funciona**: 100% de erro, o kernel mata o Ollama ao carregar o modelo (OOM) | **6 GB** (4 GB medido + contexto real + Ubuntu) e **pelo menos 2 vCPU**: com 1 vCPU a resposta leva 16 s e fura a meta de 10 s do cliente (D15) |
| **monitor** | Prometheus + Alertmanager + Grafana + blackbox | **Memória** | Funciona para a equipe: 157 MB parado. Com ~80 painéis abertos ao mesmo tempo chegou a 485 MB (95%) e consultas de 20 s | 512 MB atende o uso normal; 1 GB dá folga para o SO |

## 2. Por que o monitoramento numa VM nova?

A pergunta é se faz sentido colocar Prometheus, Alertmanager e Grafana numa
quarta VM em vez de dentro de uma das três que já existem. O teste responde
com números:

1. **O monitor precisa sobreviver à falha que ele avisa.** No teste do back a
   sonda da VM de monitoramento disparou `ApiForaDoAr` (crítico) enquanto o
   back estava com a CPU em 100%. Se o Prometheus estivesse na mesma VM, ele
   estaria disputando a mesma CPU travada e a mesma memória: o alerta
   atrasaria ou não sairia, justamente quando é mais necessário.
2. **Nenhuma VM existente tem sobra.** O monitoramento usa 157 MB parado e
   até ~485 MB sob uso pesado.
   - No **back** (já com 338 MB de pico + Ubuntu) isso estoura os 512 MB e o
     OOM killer escolheria entre o Postgres e o gunicorn.
   - Na **IA** cada MB a menos é modelo que não carrega (a IA já não cabe em
     3 GB).
   - O **front** tem memória sobrando (25 MB), mas é a única VM exposta à
     rede (porta 80). Colocar o Grafana e o Prometheus nela aumenta a
     superfície de ataque, e uma sobrecarga de acessos ao site (CPU 100% com
     ~300 usuários) derrubaria o monitoramento junto.
3. **Visão de fora.** A sonda (blackbox) testa os serviços pela rede, como o
   usuário. Numa VM separada, uma falha de rede ou de firewall aparece; na
   mesma VM, o teste por `localhost` passaria mesmo com o site inacessível.
4. **Ciclos de vida separados.** O deploy automático reinicia o back a cada
   push no `develop`; o histórico do Prometheus (15 dias) cresce no disco da
   VM dele, não no disco dos PDFs.
5. **Segurança.** Uma só máquina (10.10.10.8) é autorizada a ler as portas de
   métricas (9100, 9187, 8000/metrics) de cada VM no ufw. As VMs da aplicação
   não ganham porta nova aberta para a rede.

**Custo e como ele é coberto:** é mais uma VM para manter (512 MB a 1 GB) e
um novo ponto de falha. Para o "quem vigia o vigia", existe o alerta
`Watchdog`, que fica sempre disparado e deve ir para um heartbeat externo:
se ele parar de chegar, a VM de monitoramento caiu.

**Alternativa barata, se não houver memória para a 4ª VM:** juntar o
monitoramento ao front (a VM com mais sobra de RAM), mantendo Grafana e
Prometheus só na rede interna. Funciona, mas perde os pontos 1 e 5 para o
front.

## 3. Como foi testado

```
 k6 (carga) ──► vm-front   10.10.10.6  nginx + build do Vue            512 MB / 1 vCPU
            ──► vm-back    10.10.10.5  gunicorn + 2 Postgres          512 MB / 1 vCPU
            ──► vm-ia      10.10.10.7  Ollama (bge-m3, llama3.2)      512 MB / 1 vCPU
            ──► vm-monitor 10.10.10.8  Prometheus+Alertmanager+Grafana 512 MB / 1 vCPU
```

- Cada VM é **um container com todos os processos dela**: os processos
  dividem os mesmos 512 MB, como na VM. As imagens usam os arquivos reais do
  deploy (`deploy/gunicorn.conf.py`, parâmetros do
  `deploy/docker-compose.prod.yml`, mesmo `nginx` e mesmo build do front).
- Os IPs são os mesmos da Rede NAT `akavision-net`.
- As quatro VMs ficaram ligadas o tempo todo e a vm-monitor coletou todas,
  avaliando os alertas de verdade durante os testes.
- Para cada VM: reinício, 60 s parada (linha de base), carga em degraus, 30 s
  de resfriamento. CPU e memória gravadas a cada ~1 s (`docker stats`) e
  eventos de OOM (`docker events`).

| VM | Carga aplicada |
| --- | --- |
| front | página inteira (HTML+JS+CSS com gzip): 25 → 100 → 300 → 600 usuários |
| back | telas comuns logado: 10 → 30 → 60 → 120 usuários; depois login: 1 → 3 → 6 → 10 por segundo |
| ia | embedding (bge-m3): 1 → 2 → 4 simultâneos; resposta (llama3.2): 1 → 3 → 6 simultâneas |
| monitor | painéis do Grafana abertos (refresh 15 s, 12 consultas de 1 h): 5 → 20 → 50 → 100 → 200 |

## 4. Resultados por VM

### front — gargalo: CPU

| Medida | Valor |
| --- | --- |
| Memória parada / pico | 12,5 MB / 24,8 MB (4,8% de 512 MB) |
| CPU | 13% com 25 usuários, 50% com 100, 100% a partir de ~300 |
| Vazão máxima | ~550 req/s (135 mil requisições, 0 erro) |
| Latência p95 | 5 ms até 100 usuários; 600 ms com 300; 2,1 s com 600 |

A memória quase não se mexe: o nginx serve arquivos prontos. O limite é a
CPU (compressão gzip e conexões). Mesmo com 600 usuários não houve erro, só
fila. 512 MB é mais do que suficiente.

### back — gargalo: CPU, com memória apertada

| Medida | Valor |
| --- | --- |
| Memória parada / pico / depois | 270 MB / 338 MB (66%) / 309 MB |
| CPU | 94% já com 10 usuários; 100% a partir de 30 |
| Vazão máxima (telas comuns) | ~45 req/s com 10 usuários; travou em ~40 req/s |
| Latência p95 (telas comuns) | 33 ms com 10 usuários; 800 ms com 30; 2,9 s com 60; 3,8 s com 120 |
| Login | ~0,25 s de CPU cada (PBKDF2); 3/s usa 75% da CPU; 6/s → p95 de 11 s a 53 s |
| Erros | 0,18% (21 logins e 3 telas passaram de 60 s) |
| Alertas que dispararam | `ApiForaDoAr` (crítico: a sonda de 5 s não teve resposta), `ApiLenta` e `ApiMuitoLenta` |

- A **CPU** quebra primeiro: o ponto de ruptura (p95 > 500 ms) foi com ~26
  usuários navegando ao mesmo tempo. No cenário do trabalho (40 analistas,
  pico de ~30 req/min) isso atende, mas sem folga para rajadas.
- O **login é a rota mais cara**: o hash da senha é lento de propósito
  (segurança contra força bruta). Uma rajada de logins no começo do
  expediente (ex.: 40 pessoas entrando no mesmo minuto) já enfileira.
- A **memória** não estourou no container, mas o container não tem o Ubuntu.
  Numa VM real somam-se ~150 MB do sistema (systemd, sshd, Docker, runner do
  GitHub): ~490 MB de 512 MB. E o `deploy/docker-compose.prod.yml` configura
  o Postgres rag com `shared_buffers=512MB`, **maior que a VM inteira**: com
  uma base de vetores maior, o Postgres vai crescer até o OOM killer agir.
- **Recomendação:** 1 GB e 2 vCPU; com 512 MB, baixar `shared_buffers` para
  128 MB e o gunicorn para 2 workers.

### ia — gargalo: memória (não funciona com 512 MB)

| Medida | Valor |
| --- | --- |
| Resultado com 512 MB | **100% de erro** (121 de 121): `llama-server process has terminated: signal: killed` |
| Eventos de OOM | 2 no teste de carga; o kernel matou o processo do modelo a cada tentativa |
| Memória | sobe até 512 MB (100%) ao carregar o modelo e cai de novo |

O Ollama precisa colocar o modelo **inteiro** na RAM. O teste de
dimensionamento (`dimensionar_ia.py`) recriou a VM com mais memória:

| RAM | bge-m3 (embedding) | llama3.2 (resposta) | Pico de memória |
| --- | --- | --- | --- |
| 512 MB | OOM | OOM | 512 MB |
| 1 GB | OOM | OOM | 939 MB |
| 1,5 GB | ok (7,1 s para carregar) | OOM | 1,5 GB |
| 2 GB | ok | OOM | 2,0 GB |
| 3 GB | ok | OOM (o bge-m3 continua carregado) | 3,0 GB |
| **4 GB** | ok | **ok** | **3,5 GB** |

Com 4 GB funcionando, a CPU decide a velocidade:

| vCPU | Resposta (modelo já carregado) | Tokens/s | Embedding |
| --- | --- | --- | --- |
| 1 | 16,4 s | 2,2 | 1,9 s |
| 2 | 6,9 s | 5,5 | 0,9 s |
| 4 | 3,3 s | 13,4 | 0,8 s |

- **Memória** decide se a IA funciona: no container o mínimo foi **4 GB**,
  porque cada pergunta usa os dois modelos e eles ficam carregados juntos
  (~3,5 GB).
- **Na VM real a recomendação é 6 GB.** Ao pico medido somam-se:

  | Parte | Memória |
  | --- | --- |
  | Pico medido (bge-m3 + llama3.2 juntos) | ~3,55 GB |
  | Contexto real: o teste usou `num_ctx` 2048, o `responder.py` usa 4096 (cache de ~110 KB por token no llama3.2) | +~0,22 GB (estimado) |
  | Ubuntu Server + node-exporter (o container não tem) | +~0,2 a 0,3 GB |
  | **Total esperado** | **~4,0 a 4,1 GB** |

  Uma VM de 4 GB ficaria no limite e daria OOM em perguntas com trechos
  longos. Com 6 GB sobra ~1,9 GB para picos e para o alerta de memória
  avisar antes de quebrar; 5 GB é o mínimo, com pouca folga. Mais memória
  não deixa a IA mais rápida: isso é a CPU (6 GB e 4 vCPU é o ideal).
- Se não houver 6 GB: `OLLAMA_MAX_LOADED_MODELS=1` (um modelo por vez,
  ~2,5 GB, mas +7 a 13 s por pergunta para recarregar) ou `llama3.2:1b`
  (~1,3 GB a menos, respostas piores).
- **CPU** decide o tempo de resposta: de 1 para 4 vCPU a resposta ficou 5x
  mais rápida. Com 1 vCPU, 6 perguntas simultâneas levariam ~100 s cada (fila).
- **Meta do cliente (D15): resposta da IA em menos de 10 s.** Com 1 vCPU
  (16,4 s) a meta não é cumprida; com 2 vCPU (6,9 s) é; com 4 vCPU (3,3 s)
  sobra folga para perguntas simultâneas. Por isso a IA precisa de **6 GB e
  pelo menos 2 vCPU**.

### monitor — gargalo: memória

| Medida | Valor |
| --- | --- |
| Memória parada / pico / depois | 157 MB / 485 MB (95%) / 353 MB |
| CPU | 35% a 75% até 50 painéis; 100% a partir de ~90 |
| Latência p95 das consultas | ~85 ms até 50 painéis; 7,4 s com 90; 29 s com 200 |
| Erros | 0,05% (4 de 7.962) |

Com o uso real (a equipe com o painel aberto, <10 pessoas) a VM fica em
~200 MB e responde em menos de 100 ms. O limite aparece com ~80 painéis
abertos ao mesmo tempo: a memória chegou a 95% e não voltou toda (353 MB
depois), porque o Grafana e o Prometheus guardam cache. Por isso o alerta
mais sensível desta VM é de memória.

## 5. Alertas por máquina

Arquivo: `monitoring/prometheus/alertas-vms.yml` (28 regras, validadas com
`promtool`). Cada VM roda um node-exporter e o Prometheus da VM de
monitoramento coloca o rótulo `vm` (`monitoring/prometheus/prometheus-vms.yml`).

**Todas as VMs**

| Alerta | Condição | Severidade |
| --- | --- | --- |
| VmForaDoAr | node-exporter sem resposta por 1 min | crítico |
| VmMatouProcessoPorFaltaDeMemoria | OOM killer agiu nos últimos 10 min | crítico |
| VmComPressaoDeMemoria | processos esperando memória > 10% do tempo por 5 min | atenção |
| VmUsandoSwap | swap > 50% por 10 min | atenção |
| VmDiscoQuaseCheio / VmDiscoCritico | disco > 80% / > 90% | atenção / crítico |

**Por VM, no recurso que satura primeiro**

| VM | Alerta | Condição | Severidade | De onde veio o limite |
| --- | --- | --- | --- | --- |
| front | FrontCpuAlta | CPU > 80% por 5 min | atenção | CPU chegou a 100% com ~300 usuários |
| front | FrontMuitasConexoes | > 250 conexões no nginx por 5 min | atenção | p95 > 500 ms a partir de ~300 |
| front | FrontMemoriaAnormal | memória > 70% por 10 min | atenção | o nginx nunca passou de 5%: isso é anomalia |
| front | FrontLento | página > 1 s por 5 min | atenção | SLO das rotas comuns |
| back | BackCpuAlta | CPU > 85% por 5 min | atenção | CPU 100% a partir de ~26 usuários |
| back | BackLoginLento | p95 do login > 2 s por 5 min | atenção | 6 logins/s → 11 s |
| back | BackFilaDeCpu | load5 > 2x vCPUs por 5 min | crítico | fila de requisições |
| back | BackMemoriaAlta / Critica | memória > 80% / > 92% | atenção / crítico | pico de 66% sem o SO; com o SO ~95% |
| ia | IaVmComMenosDe6GB | RAM total < 6 GB | atenção | abaixo do recomendado: perguntas longas podem dar OOM |
| ia | IaVmSemMemoriaParaOModelo | RAM total < 4,5 GB | crítico | modelos + contexto + SO (~4,1 GB) não cabem |
| ia | IaMemoriaNoLimite | memória livre < 8% por 2 min | crítico | aviso antes do OOM que derrubou a IA no teste |
| ia | IaCpuSaturadaMuitoTempo | CPU > 95% por 15 min | atenção | 100% durante a resposta é normal; 15 min é fila |
| monitor | MonitorMemoriaAlta | memória > 85% por 5 min | atenção | chegou a 95% com ~80 painéis |
| monitor | MonitorConsultasLentas | p95 das consultas > 2 s por 5 min | atenção | passou de 2 s com ~80 painéis |
| monitor | PrometheusFalhaNasRegras / AlertmanagerNaoEntrega | qualquer falha em 10 min | crítico | alerta que não sai é pior que alerta falso |
| monitor | Watchdog | sempre disparado | heartbeat | quem vigia o vigia |

Os alertas de aplicação já existentes (`alertas.yml`: API/IA fora do ar,
latência, erros 5xx, IA lenta, falhas de login, exportação de dados
pessoais) continuam valendo. Os limites de `IaLenta` e `IaMuitoLenta` foram
ajustados à meta do cliente: p95 da resposta da IA **> 10 s** (atenção, D15)
e **> 30 s** (crítico, 3x a meta); antes eram 45 s e 120 s. `ApiForaDoAr`, `ApiLenta` e `ApiMuitoLenta`
dispararam de verdade durante o stress do back.

## 6. Limitações

- **Container não é VM.** Sem kernel e sem os ~150 MB do Ubuntu; os números
  de memória são otimistas, por isso as recomendações somam o SO.
- O gerador de carga roda no mesmo PC (12 núcleos); o limite de 1 vCPU por
  VM é aplicado pelo Docker (cgroups).
- Sem node-exporter dentro dos containers (ele leria o PC inteiro), os
  alertas de infraestrutura por VM foram validados com `promtool`, não
  disparados no laboratório. Os de aplicação dispararam de verdade.
- A rota da pergunta (`/api/perguntas/`) não entrou no stress do back: ela
  depende da IA, que foi medida diretamente no Ollama.
- Os eventos `die` em `marcos.json` são o reinício da VM antes de cada teste,
  não quedas.

## Estimativa: back com 300 usuários simultâneos

Não foi testado; é uma conta feita com os números medidos no back com 1 vCPU.

**Base medida:** ~40 req/s por vCPU nas telas comuns (~25 ms de CPU cada);
login com ~0,25 s de CPU; ~270 MB parado com 3 workers e 2 Postgres. Para
sobrar folga, cada vCPU deve trabalhar a no máximo 70%: **~28 req/s por vCPU**.

| Cenário | Comportamento | Carga | CPU necessária |
| --- | --- | --- | --- |
| A. Realista | 300 logados, um clique a cada ~20 s, ~4 chamadas por clique | ~60 req/s, mais 300 logins em 2 min (~0,6 vCPU) | ~3 vCPU, **4 vCPU** com folga |
| B. Pior caso | 300 usuários como no k6: 1 requisição/s sem parar | ~300 req/s | **~11 vCPU**: 3 VMs de 4 vCPU |

| Componente | Hoje (40 analistas) | 300, cenário A | 300, pior caso |
| --- | --- | --- | --- |
| back (Django) | 1 GB / 2 vCPU | **4 GB / 4 vCPU** | 3 VMs de 2 GB / 4 vCPU, com o nginx do front repartindo a carga |
| banco (Postgres) | junto do back | **VM própria, 4 GB / 2 vCPU**, `shared_buffers` ~1 GB | VM própria, 8 GB / 4 vCPU, com PgBouncer (3 × 36 conexões passa do limite de 100) |
| gunicorn | 3 workers × 4 threads | 9 workers × 4 threads (2 × vCPU + 1) | 9 × 4 em cada VM |

- **Memória do back:** 9 workers × ~70 MB ≈ 650 MB, mais ~300 MB do Ubuntu e
  do resto, dá 4 GB com folga. O Postgres sai para uma VM própria porque, com
  300 usuários, precisa de ~1 GB de cache e no teste disputava a CPU com o
  Django.
- **Nessa escala o gargalo passa a ser a IA.** Com 4 vCPU ela dá uma resposta
  a cada ~3,3 s (~18 por minuto). Se cada pessoa fizer 1 pergunta a cada
  10 min, são ~30 por minuto: a fila cresce e a meta D15 (< 10 s) é
  descumprida. Cada pergunta também prende uma thread do gunicorn enquanto
  espera; com 12 threads, 12 perguntas simultâneas travam as telas comuns.
  Para 300 usuários a IA precisaria de **GPU** (ou várias VMs de IA), e a
  pergunta teria que ir para uma **fila assíncrona** no back.
- **Limite da estimativa:** a conta supõe que a capacidade cresce em linha
  reta com as CPUs; na prática cresce um pouco menos. Para confirmar, basta
  rodar o mesmo k6 com 300 usuários e pausa de ~20 s entre cliques.

## Ligação com o que o cliente pediu

| Requisito do cliente | O que o teste mostrou | Alerta que vigia |
| --- | --- | --- |
| D1 / US-01: IA rodando localmente, sem API externa | Com 512 MB a IA não funciona (OOM); precisa de 6 GB | `IaVmComMenosDe6GB`, `IaVmSemMemoriaParaOModelo`, `OllamaForaDoAr` |
| D15: busca por termo em menos de 2 s | O back cumpre até ~26 usuários simultâneos com 1 vCPU | `ApiMuitoLenta` (p95 > 2 s), `BackLoginLento` |
| D15: resposta da IA em menos de 10 s | 1 vCPU: 16,4 s (não cumpre); 2 vCPU: 6,9 s; 4 vCPU: 3,3 s | `IaLenta` (p95 > 10 s), `IaMuitoLenta` (> 30 s) |
| US-28: alerta de login anormal e download em massa | — | `MuitasFalhasDeLogin`, `PossivelForcaBruta`, `MuitasExportacoesDeDadosPessoais` |
| D9 / D11 / LGPD: dados protegidos | O monitor isolado deixa as portas de métricas fora da internet | firewall só libera o IP do monitor |

O teste de stress mostra quanto de máquina cada meta exige; os alertas avisam
no momento em que alguma deixar de ser cumprida.
