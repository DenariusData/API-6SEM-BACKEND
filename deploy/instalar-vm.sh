#!/usr/bin/env bash
# Instala o AkaVision numa VM Ubuntu Server. Roda uma vez por VM; depois
# disso, o deploy de cada push no develop é feito pelo GitHub Actions
# (.github/workflows/deploy.yml nos dois repositórios).
#
# Papel da VM (primeiro argumento):
#   tudo   (padrão) front, back, bancos e IA na mesma VM
#   front  nginx com o build do front; /api vai para a VM do back  --ip-back
#   back   Django (gunicorn) e os bancos; a IA fica na VM da IA     --ip-front --ip-ia
#   ia     só o Ollama (bge-m3 e llama3.2), aberto só para o back   --ip-back
#
# --url-site (opcional, papel back): endereço que o navegador usa para abrir
# o site, para os links dos e-mails (ex.: http://192.168.15.10 quando o site
# chega por redirecionamento de porta do VirtualBox). Padrão: http://<ip-front>.
#
# Pré-requisitos já instalados (ver deploy/README.md):
#   tudo: git, nginx, Docker, Node 22 e Ollama com os modelos
#   front: git, nginx e Node 22 | back: git e Docker | ia: Ollama
#
# Rodar como root, desligado do terminal (o SSH pode cair sem parar o script):
#   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root \
#     bash instalar-vm.sh back --ip-front 10.10.10.6 --ip-ia 10.10.10.7
#   journalctl -u instalar-akavision -f
#
# Pode ser rodado de novo: pula o que já existe. As senhas do .env são
# geradas uma vez e nunca trocadas; os endereços (IPs) são atualizados.
set -euo pipefail
passo() { echo; echo "==> $*"; }
erro() { echo "ERRO: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || erro "rode como root (sudo)."

PAPEL=${1:-tudo}
[[ $# -gt 0 ]] && shift
IP_FRONT="" IP_BACK="" IP_IA="" URL_SITE=""
while [[ $# -gt 0 ]]; do
  [[ $# -ge 2 ]] || erro "$1 precisa de um valor depois dele."
  case "$1" in
    --ip-front) IP_FRONT=$2 ;;
    --ip-back) IP_BACK=$2 ;;
    --ip-ia) IP_IA=$2 ;;
    --url-site)
      [[ $2 =~ ^https?://[A-Za-z0-9.:-]+/?$ ]] || erro "--url-site: '$2' não é o endereço de um site (protocolo, IP ou nome e porta opcional)."
      URL_SITE=${2%/}
      ;;
    *) erro "opção desconhecida: $1" ;;
  esac
  shift 2
done

precisa() {
  local opcao=$1 valor=$2
  [[ -n $valor ]] || erro "o papel '$PAPEL' precisa de --$opcao <IP>."
  [[ $valor =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || erro "--$opcao: '$valor' não é um IP."
}
case "$PAPEL" in
  tudo) ;;
  front) precisa ip-back "$IP_BACK" ;;
  back) precisa ip-front "$IP_FRONT"; precisa ip-ia "$IP_IA" ;;
  ia) precisa ip-back "$IP_BACK" ;;
  *) erro "papel '$PAPEL' desconhecido: use tudo, front, back ou ia." ;;
esac
# "tem back": esta VM faz o papel do back (sozinha ou junto com tudo).
tem() {
  local papel=$1
  [[ $PAPEL == tudo || $PAPEL == "$papel" ]]
}

BASE=/opt/akavision
BACK=$BASE/backend
FRONT=$BASE/frontend
WWW=/var/www/akavision
UPLOADS=$BASE/uploads/documentos
IP=$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1)}')
como_deploy() { sudo -u deploy -H bash -c "$1"; }
echo "Papel desta VM: $PAPEL | IP: $IP"

if tem front || tem back; then
  passo "Usuário deploy (roda o backend, o front e o runner do GitHub)"
  id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
  passwd -l deploy >/dev/null
  usermod -aG systemd-journal deploy  # para o deploy.yml mostrar o log em caso de erro
  install -d -o deploy -g deploy "$BASE"
  if tem back; then install -d -o deploy -g deploy "$BASE/uploads" "$UPLOADS"; fi
  if tem front; then install -d -o deploy -g deploy "$WWW"; fi

  # O repositório do back também traz os arquivos de deploy (nginx) do front.
  passo "Código do GitHub (branch develop)"
  [[ -d $BACK/.git ]] || como_deploy "git clone -b develop https://github.com/DenariusData/API-6SEM-BACKEND.git $BACK"
  como_deploy "git -C $BACK pull --ff-only"
  if tem front; then
    [[ -d $FRONT/.git ]] || como_deploy "git clone -b develop https://github.com/DenariusData/API-6SEM-FRONTEND.git $FRONT"
    como_deploy "git -C $FRONT pull --ff-only"
  fi
fi

if tem back; then
  passo ".env do backend (senhas geradas aqui, nunca vão para o git)"
  if [[ ! -f $BACK/.env ]]; then
    PW_RAG=$(openssl rand -hex 24)
    PW_CRED=$(openssl rand -hex 24)
    cat > "$BACK/.env" <<ENV
DEBUG=False
SECRET_KEY=$(openssl rand -hex 48)
JWT_SECRET=$(openssl rand -hex 48)
DEFAULT_DB_NAME=rag
DEFAULT_DB_USER=rag
DEFAULT_DB_PASSWORD=$PW_RAG
DEFAULT_DB_HOST=localhost
DEFAULT_DB_PORT=5434
CREDENCIAIS_DB_NAME=credenciais
CREDENCIAIS_DB_USER=credenciais
CREDENCIAIS_DB_PASSWORD=$PW_CRED
CREDENCIAIS_DB_HOST=localhost
CREDENCIAIS_DB_PORT=5433
DATABASE_URL=postgresql://rag:$PW_RAG@localhost:5434/rag
PROCESSAMENTO_AUTOMATICO=true
DOCUMENTOS_UPLOAD_DIR=$UPLOADS
ENV
    chown deploy:deploy "$BACK/.env"
    chmod 600 "$BACK/.env"
  fi
  # Endereços: dependem do papel e são atualizados a cada execução.
  definir_env() {
    local chave=$1 valor=$2
    if grep -q "^$chave=" "$BACK/.env"; then
      sed -i "s#^$chave=.*#$chave=$valor#" "$BACK/.env"
    else
      echo "$chave=$valor" >> "$BACK/.env"
    fi
  }
  # http entre as VMs: o tráfego fica na rede interna (Rede NAT do
  # VirtualBox) e o firewall de cada VM só aceita o IP de quem precisa
  # dela. HTTPS entre VMs fica para quando houver certificado interno.
  if [[ $PAPEL == back ]]; then
    # O nginx do front chama o back com o Host "<IP do back>:8000", seja
    # qual for o endereço que o navegador usou (IP do front ou do PC).
    definir_env ALLOWED_HOSTS "$IP,localhost,127.0.0.1"
    definir_env FRONTEND_URL "${URL_SITE:-http://$IP_FRONT}"
    definir_env OLLAMA_URL "http://$IP_IA:11434"
    definir_env GUNICORN_BIND "0.0.0.0:8000"
  else
    definir_env ALLOWED_HOSTS "$IP,localhost,127.0.0.1"
    definir_env FRONTEND_URL "http://$IP"
    definir_env OLLAMA_URL "http://localhost:11434"
    definir_env GUNICORN_BIND "127.0.0.1:8000"
  fi

  passo "Bancos (Docker, só em 127.0.0.1)"
  systemctl enable --now docker
  docker compose --project-directory "$BACK" -f "$BACK/deploy/docker-compose.prod.yml" up -d
  echo "Esperando os bancos ficarem prontos..."
  for _ in $(seq 1 30); do
    if docker exec rag-postgres pg_isready -U rag >/dev/null 2>&1 \
      && docker exec credenciais-postgres pg_isready -U credenciais >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done

  passo "Python 3.12 e dependências do backend (uv)"
  [[ -x /home/deploy/.local/bin/uv ]] || como_deploy "curl -LsSf https://astral.sh/uv/install.sh | sh"
  como_deploy "cd $BACK && { [ -d .venv ] || ~/.local/bin/uv venv --python 3.12 .venv; } && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt"

  passo "Migrations e dados iniciais"
  como_deploy "cd $BACK && .venv/bin/python manage.py migrate --noinput \
    && .venv/bin/python manage.py migrate --database=credenciais_db --noinput \
    && .venv/bin/python manage.py seed_taxonomia \
    && .venv/bin/python manage.py seed_usuarios_demo"

  passo "Serviço do backend (gunicorn)"
  install -m 644 "$BACK/deploy/akavision-backend.service" /etc/systemd/system/akavision-backend.service
  systemctl daemon-reload
  systemctl enable akavision-backend
  systemctl restart akavision-backend

  passo "Permissão do usuário deploy: só reiniciar o backend"
  echo 'deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart akavision-backend' > /etc/sudoers.d/akavision-deploy
  chmod 440 /etc/sudoers.d/akavision-deploy
  visudo -cf /etc/sudoers.d/akavision-deploy
fi

if tem front; then
  passo "Build do front"
  como_deploy "cd $FRONT && npm ci && echo 'VITE_API_BASE_URL=/' > .env.production.local && npm run build && rsync -a --delete dist/ $WWW/"

  passo "Site no nginx"
  install -m 644 "$BACK/deploy/nginx-akavision.conf" /etc/nginx/sites-available/akavision
  if [[ $PAPEL == front ]]; then
    # /api/ vai para a VM do back, com o Host fixo "<IP do back>:8000": o
    # ALLOWED_HOSTS do back não depende de como o navegador chegou ao site.
    sed -i -e "s#http://127.0.0.1:8000#http://$IP_BACK:8000#" \
      -e 's#proxy_set_header Host $host;#proxy_set_header Host $proxy_host;#' \
      /etc/nginx/sites-available/akavision
    grep -q 'Host $proxy_host;' /etc/nginx/sites-available/akavision \
      || erro "nginx-akavision.conf mudou: ajuste o sed do papel front."
  fi
  ln -sf /etc/nginx/sites-available/akavision /etc/nginx/sites-enabled/akavision
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable nginx
  systemctl reload-or-restart nginx
fi

if tem ia; then
  passo "Ollama"
  command -v ollama >/dev/null || erro "Ollama não instalado (ver deploy/README.md)."
  OVERRIDE=/etc/systemd/system/ollama.service.d/akavision.conf
  if [[ $PAPEL == ia ]]; then
    # Escuta na rede; o firewall abaixo deixa entrar só o IP do back.
    install -d "$(dirname "$OVERRIDE")"
    printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\n' > "$OVERRIDE"
  else
    rm -f "$OVERRIDE"
  fi
  systemctl daemon-reload
  systemctl enable ollama
  systemctl restart ollama
  for _ in $(seq 1 30); do
    curl -fsS -o /dev/null http://127.0.0.1:11434/api/version && break
    sleep 2
  done
  for modelo in bge-m3 llama3.2; do
    ollama list | grep -q "^$modelo:" || ollama pull "$modelo"
  done
fi

passo "Serviços que não são deste papel"
desligar() {
  for servico in "$@"; do
    if systemctl cat "$servico" >/dev/null 2>&1; then
      systemctl disable --now "$servico" >/dev/null 2>&1 || true
      echo "  $servico desligado"
    fi
  done
}
if ! tem back; then
  desligar akavision-backend
  if command -v docker >/dev/null && systemctl is-active --quiet docker; then
    docker stop rag-postgres credenciais-postgres >/dev/null 2>&1 || true
  fi
  desligar docker.socket docker
fi
if ! tem front; then desligar nginx; fi
if ! tem ia; then desligar ollama; fi

passo "Firewall (ufw): só o necessário para este papel"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
if tem front; then ufw allow 80/tcp >/dev/null; fi
if [[ $PAPEL == back ]]; then ufw allow from "$IP_FRONT" to any port 8000 proto tcp >/dev/null; fi
if [[ $PAPEL == ia ]]; then ufw allow from "$IP_BACK" to any port 11434 proto tcp >/dev/null; fi
ufw --force enable >/dev/null
ufw status | sed 's/^/  /'

passo "Conferência"
sleep 3
http() {
  local url=$1
  curl -s -m 10 -o /dev/null -w '%{http_code}' "$url" || true
}
if tem back; then
  systemctl is-active akavision-backend docker || true
  docker ps --format '{{.Names}}: {{.Status}}' || true
  echo "API: HTTP $(http http://127.0.0.1:8000/api/auth/me/) (401 = no ar)"
fi
if [[ $PAPEL == back ]]; then
  echo "IA em $IP_IA: HTTP $(http "http://$IP_IA:11434/api/version") (200 = alcançável; 000 = VM da IA desligada ou sem liberar este IP)"
fi
if tem front; then
  systemctl is-active nginx || true
  echo "Site: HTTP $(http http://127.0.0.1/) (200 = no ar)"
  echo "API pelo site: HTTP $(http http://127.0.0.1/api/auth/me/) (401 = back alcançável; 502/504 = VM do back desligada)"
fi
if tem ia; then
  systemctl is-active ollama || true
  echo "Ollama: HTTP $(http http://127.0.0.1:11434/api/version) (200 = no ar)"
  ollama list || true
fi
echo
case "$PAPEL" in
  tudo) echo "PRONTO. Abra o IP $IP (porta 80) no navegador de qualquer PC da rede." ;;
  front) echo "PRONTO. Site no IP $IP, porta 80 (na Rede NAT do VirtualBox, pelo IP do PC: deploy/README.md)." ;;
  back) echo "PRONTO. O site fica no front: ${URL_SITE:-IP $IP_FRONT, porta 80}" ;;
  ia) echo "PRONTO. Ollama no IP $IP, porta 11434, aberto só para o back ($IP_BACK)." ;;
  *) echo "PRONTO." ;;
esac
