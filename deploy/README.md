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
- **As três VMs precisam de IP fixo**: se um IP mudar, a ligação entre
  elas quebra. Por isso elas ficam numa Rede NAT própria do VirtualBox
  (`akavision-net`), com o IP de cada uma fixado pelo MAC no DHCP do
  VirtualBox. Não depende do roteador da casa e vai junto com as VMs para
  outro PC (é só recriar a rede com os comandos abaixo).

### Rede NAT `akavision-net` (VirtualBox)

| VM | MAC | IP fixo | SSH a partir do PC |
| --- | --- | --- | --- |
| `akavision` (back) | 08:00:27:9c:29:f8 | 10.10.10.5 | `ssh -p 2205 vboxuser@127.0.0.1` |
| `akavision-front` | 08:00:27:e2:54:2c | 10.10.10.6 | `ssh -p 2206 vboxuser@127.0.0.1` |
| `akavision-ia` | 08:00:27:a6:20:f4 | 10.10.10.7 | `ssh -p 2207 vboxuser@127.0.0.1` |

O site abre pelo IP do PC que roda as VMs (`http://<IP do PC>`): a porta 80
do PC é redirecionada para o front. Só o front e o SSH ficam acessíveis de
fora; o back e a IA só são alcançados de dentro da rede.

Criar a rede (no PC, PowerShell; troque os MACs se as VMs forem outras):

```powershell
$vb = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
& $vb natnetwork add --netname akavision-net --network 10.10.10.0/24 --enable --dhcp on
& $vb natnetwork modify --netname akavision-net --port-forward-4 "site:tcp:[]:80:[10.10.10.6]:80"
& $vb natnetwork modify --netname akavision-net --port-forward-4 "ssh-back:tcp:[]:2205:[10.10.10.5]:22"
& $vb natnetwork modify --netname akavision-net --port-forward-4 "ssh-front:tcp:[]:2206:[10.10.10.6]:22"
& $vb natnetwork modify --netname akavision-net --port-forward-4 "ssh-ia:tcp:[]:2207:[10.10.10.7]:22"
& $vb dhcpserver add --network=akavision-net --server-ip=10.10.10.3 --netmask=255.255.255.0 --lower-ip=10.10.10.100 --upper-ip=10.10.10.200 --enable
& $vb dhcpserver modify --network=akavision-net --set-opt=3 10.10.10.1 --set-opt=6 10.10.10.1
& $vb dhcpserver modify --network=akavision-net --mac-address=08:00:27:9c:29:f8 --fixed-address=10.10.10.5
& $vb dhcpserver modify --network=akavision-net --mac-address=08:00:27:e2:54:2c --fixed-address=10.10.10.6
& $vb dhcpserver modify --network=akavision-net --mac-address=08:00:27:a6:20:f4 --fixed-address=10.10.10.7
# Cada VM desligada: placa de rede 1 na Rede NAT
& $vb modifyvm akavision --nic1 natnetwork --nat-network1 akavision-net
```

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

   A VM desliga no fim. Ligar a placa de rede na Rede NAT `akavision-net`
   (acima) e ligar a VM.
3. Rodar o `instalar-vm.sh` com o papel de cada VM:

   ```bash
   # VM da IA
   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
     bash instalar-vm.sh ia --ip-back 10.10.10.5
   # VM do front
   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
     bash instalar-vm.sh front --ip-back 10.10.10.5
   # VM do back (a original); --url-site é o endereço que o navegador usa
   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
     bash instalar-vm.sh back --ip-front 10.10.10.6 --ip-ia 10.10.10.7 \
     --url-site http://<IP do PC>
   journalctl -u instalar-akavision -f
   ```

   No back, o script ajusta no `.env` só os endereços (`ALLOWED_HOSTS`,
   `FRONTEND_URL`, `OLLAMA_URL`, `GUNICORN_BIND`); as senhas não mudam.
   O nginx do front chama o back com o Host `10.10.10.5:8000`, então o
   back aceita o site aberto por qualquer endereço (IP do PC ou do front).
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
