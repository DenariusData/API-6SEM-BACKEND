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
| `instalar-vm.sh` | instalação inicial (uma vez): usuário, código, `.env`, bancos, migrations, serviço, front e nginx |
| `docker-compose.prod.yml` | os dois Postgres, com senha do `.env` e porta só local |
| `gunicorn.conf.py` | servidor de aplicação: 3 workers x 4 threads, timeout de 620 s para a IA |
| `akavision-backend.service` | serviço systemd do gunicorn (sobe com a VM, reinicia se cair) |
| `nginx-akavision.conf` | site na porta 80: front estático e `/api/` para o gunicorn |
| `../.github/workflows/deploy.yml` | deploy automático do backend a cada push no `develop` |

## Instalação

Na VM, com git, nginx, Docker, Node 22 e Ollama (`bge-m3` e `llama3.2`) já
instalados:

```bash
curl -fsSL https://raw.githubusercontent.com/DenariusData/API-6SEM-BACKEND/develop/deploy/instalar-vm.sh -o ~/instalar-vm.sh
sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root bash /home/$USER/instalar-vm.sh
journalctl -u instalar-akavision -f
```

Depois, registrar o runner self-hosted da organização na VM, como usuário
`deploy` e com a label `akavision`.

## Deploy automático

`deploy.yml` (backend): `git reset` para o commit do push, `uv pip install`,
migrations nos dois bancos, `systemctl restart akavision-backend` e espera a
API responder 401 em `/api/auth/me/`.

O usuário `deploy` só tem permissão de `sudo` para reiniciar o backend. Uma
mudança em `akavision-backend.service` ou em `nginx-akavision.conf` não é
aplicada pelo deploy automático: rode de novo o `instalar-vm.sh`.

## Comandos úteis na VM

```bash
sudo systemctl status akavision-backend   # backend
journalctl -u akavision-backend -f        # log do backend
sudo systemctl status nginx ollama docker
docker ps                                 # bancos
```
