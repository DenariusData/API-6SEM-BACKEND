# Teste de stress por VM (512 MB)

Laboratório que repete as VMs do AkaVision em containers com **512 MB de RAM
(sem swap) e 1 vCPU cada**, aplica carga crescente em cada uma e mede o que
satura primeiro. O relatório com os números e a análise está em
[`RELATORIO.md`](RELATORIO.md).

```
                 rede akavision-net 10.10.10.0/24 (mesmos IPs da Rede NAT do VirtualBox)
 k6 (carga) ──► vm-front   10.10.10.6  nginx + build do Vue
            ──► vm-back    10.10.10.5  gunicorn 3x4 + Postgres rag (pgvector) + Postgres credenciais
            ──► vm-ia      10.10.10.7  Ollama (bge-m3, llama3.2)
            ──► vm-monitor 10.10.10.8  Prometheus + Alertmanager + Grafana + blackbox
```

Cada VM é **um** container com todos os processos dela, então os processos
dividem os mesmos 512 MB como dividiriam na VM (no back, o gunicorn e os dois
Postgres disputam a mesma memória). O gerador de carga (k6) fica fora do
limite: ele é o "usuário".

## Rodar

Pré-requisitos: Docker Desktop e os modelos do Ollama já baixados no PC
(`ollama pull bge-m3` e `ollama pull llama3.2`), que são montados na vm-ia.

```bash
# da raiz do backend (o front precisa estar em ../API-6SEM-FRONTEND)
docker compose -f monitoring/stress/docker-compose.stress.yml up -d --build
python monitoring/stress/executar_stress.py todas     # ~40 min, as 4 VMs
python monitoring/stress/dimensionar_ia.py            # ~15 min, RAM/CPU da IA
python monitoring/stress/analisar.py                  # gera resultados/resumo.json
docker compose -f monitoring/stress/docker-compose.stress.yml down -v
```

Para outro tamanho de VM: `VM_MEMORIA=1g VM_CPUS=2 docker compose ... up -d`.

## Cenários (k6/)

| VM | Cenário | Carga |
| --- | --- | --- |
| front | página inteira (HTML + JS + CSS com gzip), 1 s entre aberturas | 25 → 100 → 300 → 600 usuários |
| back | telas comuns logado (me, listas, acervo, meus documentos, auditoria) e depois rajadas de login | 10 → 30 → 60 → 120 usuários; login 1 → 3 → 6 → 10/s |
| ia | embedding da pergunta (bge-m3) e resposta (llama3.2), as chamadas do `pergunta.py` | 1 → 2 → 4 e 1 → 3 → 6 simultâneas |
| monitor | pessoas com o painel do Grafana aberto (refresh 15 s, 12 consultas de 1 h) enquanto o Prometheus coleta as VMs | 5 → 20 → 50 → 100 → 200 painéis |

## O que é gravado (resultados/&lt;vm&gt;/)

| Arquivo | Conteúdo |
| --- | --- |
| `recursos.csv` | CPU e memória das 4 VMs a cada ~1 s (`docker stats`) |
| `k6-resumo.json`, `k6.csv.gz` | latência, vazão e erros de cada requisição |
| `marcos.json` | início/fim de cada fase, eventos de OOM, se a VM caiu e os alertas que dispararam no Prometheus |
| `vm-log.txt` | log da VM durante o teste |

## Diferenças para as VMs reais

- Container não é VM: não tem kernel próprio nem os ~150 MB do Ubuntu
  (systemd, journald, sshd, runner do GitHub). Numa VM de 512 MB sobra
  **menos** memória para a aplicação do que aqui, então os resultados são
  otimistas.
- `docker stats` mostra a memória em uso sem o cache de arquivos.
- Os dois Postgres do back rodam como dois processos no mesmo container, não
  como dois containers Docker; os parâmetros (shared_buffers etc.) são os
  mesmos do `deploy/docker-compose.prod.yml`.
- Sem node-exporter dentro dos containers (ele leria a máquina inteira do
  Docker), os alertas de infraestrutura por VM (`alertas-vms.yml`) foram
  validados com `promtool`; os alertas de aplicação (sondas, latência, IA)
  rodaram de verdade na vm-monitor durante o teste.
