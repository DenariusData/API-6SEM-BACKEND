# Deploy na VM

O sistema roda numa VM Ubuntu Server na rede local. A cada push no `develop`
(inclusive o merge de um PR), o GitHub Actions atualiza a VM sozinho.

## Como fica a VM

```
navegador ──► nginx :80 ──┬─ /api/... ──► gunicorn 127.0.0.1:8000 (Django)
                          │                 ├─► Postgres rag          127.0.0.1:5434 (Docker, pgvector)
                          │                 ├─► Postgres credenciais  127.0.0.1:5433 (Docker)
                          │                 └─► Ollama                127.0.0.1:11434 (bge-m3, llama3.2)
                          └─ resto ─────► /var/www/akavision (build do front)
```

- Só as portas 22 (SSH) e 80 (site) ficam abertas na rede (ufw). Bancos,
  backend e Ollama escutam só em `127.0.0.1`.
- Código em `/opt/akavision/backend` e `/opt/akavision/frontend`, do usuário
  `deploy`, que também roda o backend e o runner do GitHub Actions.
- O `.env` com as senhas é gerado na VM pelo `instalar-vm.sh` e nunca vai
  para o git.

## Arquivos

| Arquivo | Para quê |
| --- | --- |
| `instalar-vm.sh` | instalação inicial de cada VM (papel `tudo`, `front`, `back` ou `ia`): usuário, código, `.env`, bancos, migrations, serviço, front, nginx, Ollama e firewall |
| `preparar-clone.sh` | prepara um clone da VM original para virar a VM do front ou da IA |
| `docker-compose.prod.yml` | os dois Postgres, com senha do `.env` e porta só local |
| `gunicorn.conf.py` | servidor de aplicação: 3 workers x 4 threads, timeout de 620 s para a IA; endereço em `GUNICORN_BIND` |
| `akavision-backend.service` | serviço systemd do gunicorn (sobe com a VM, reinicia se cair) |
| `nginx-akavision.conf` | site na porta 80: front estático e `/api/` para o gunicorn (local ou na VM do back) |
| `../.github/workflows/deploy.yml` | deploy automático do backend a cada push no `develop` |

## Instalação

Na VM, com git, nginx, Docker, Node 22 e Ollama (`bge-m3` e `llama3.2`) já
instalados:

```bash
curl -fsSL https://raw.githubusercontent.com/DenariusData/API-6SEM-BACKEND/develop/deploy/instalar-vm.sh -o ~/instalar-vm.sh
sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root bash /home/$USER/instalar-vm.sh
journalctl -u instalar-akavision -f
```

Sem argumento, o script instala tudo numa VM só (papel `tudo`). Para
separar em três VMs, ver "Três VMs: front, back e IA" abaixo.

Depois, registrar o runner self-hosted da organização na VM, como usuário
`deploy` e com a label `akavision` (deploy do back) e `akavision-front`
(deploy do front). Numa VM só, um runner com as duas labels.

## Deploy automático

`deploy.yml` (backend): `git reset` para o commit do push, `uv pip install`,
migrations nos dois bancos, `systemctl restart akavision-backend` e espera a
API responder 401 em `/api/auth/me/`.

O usuário `deploy` só tem permissão de `sudo` para reiniciar o backend. Uma
mudança em `akavision-backend.service` ou em `nginx-akavision.conf` não é
aplicada pelo deploy automático: rode de novo o `instalar-vm.sh`.

## Três VMs: front, back e IA

Cada parte numa VM própria, na mesma rede. O navegador só fala com o front;
o banco e a IA não ficam expostos na rede.

```
navegador ──► VM front :80 (nginx) ──┬─ resto ──► /var/www/akavision (build do front)
                                     └─ /api/ ──► VM back :8000 (gunicorn, Django)
                                                    ├─► Postgres rag e credenciais  127.0.0.1 (Docker)
                                                    └─► VM ia :11434 (Ollama: bge-m3, llama3.2)
```

| VM | Papel | Portas abertas (ufw) | Runner do GitHub |
| --- | --- | --- | --- |
| `akavision-front` | nginx com o build do front | 22, 80 | `akavision-front` (deploy do front) |
| `akavision` | Django e os dois bancos | 22, 8000 só para o IP do front | `akavision` (deploy do back) |
| `akavision-ia` | só o Ollama | 22, 11434 só para o IP do back | nenhum: os modelos quase não mudam |

- O Ollama não tem senha e o gunicorn não tem HTTPS: por isso cada porta só
  aceita a VM que precisa dela.
- O back aceita o Ollama em outra VM só por IP da rede interna (AKA-103,
  D1: sem chamada externa).
- **As três VMs precisam de IP fixo** (reserva no roteador pelo MAC). Se um
  IP mudar, a ligação entre elas quebra; aí é só rodar o `instalar-vm.sh`
  de novo com os IPs novos.

### Montando a partir da VM única (clones do VirtualBox)

A VM original (`akavision`) vira o back: os bancos e os PDFs continuam nela.
Front e IA são clones dela, que já trazem o Ubuntu, o Node e os modelos do
Ollama.

1. No VirtualBox, com a original desligada, clonar duas vezes (clone
   completo, MAC novo) e ligar cada clone primeiro na rede **host-only**,
   sem internet: assim ele não disputa IP com a original nem pega os deploys
   com o runner que veio copiado.
2. Em cada clone, rodar o `preparar-clone.sh` (nome novo, machine-id e chaves
   SSH novos, runner e dados do back copiados apagados). O `--mac` é a trava
   que impede rodar na original:

   ```bash
   sudo bash preparar-clone.sh --nome akavision-ia --papel ia --mac <MAC do clone>
   ```

   A VM desliga no fim. Trocar a placa de rede para **bridge**, ligar e
   reservar o IP dela no roteador.
3. Rodar o `instalar-vm.sh` com o papel de cada VM (troque os IPs):

   ```bash
   # VM da IA
   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
     bash instalar-vm.sh ia --ip-back 192.168.15.5
   # VM do front
   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
     bash instalar-vm.sh front --ip-back 192.168.15.5
   # VM do back (a original)
   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
     bash instalar-vm.sh back --ip-front 192.168.15.6 --ip-ia 192.168.15.7
   journalctl -u instalar-akavision -f
   ```

   No back, o script ajusta no `.env` só os endereços (`ALLOWED_HOSTS`,
   `FRONTEND_URL`, `OLLAMA_URL`, `GUNICORN_BIND`); as senhas não mudam.
4. Registrar o runner da VM do front, como usuário `deploy` e com a label
   `akavision-front` (mesmo passo a passo do runner original). O deploy do
   front só roda depois disso.

O `testar_bge_m3_local.py` continua exigindo `localhost`: ele mede a CPU e
a RAM da máquina onde roda, então deve ser rodado na própria VM da IA.

## Comandos úteis na VM

```bash
sudo systemctl status akavision-backend   # backend
journalctl -u akavision-backend -f        # log do backend
sudo systemctl status nginx ollama docker
docker ps                                 # bancos
```
