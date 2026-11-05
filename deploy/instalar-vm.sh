#!/usr/bin/env bash
# Instala o AkaVision (backend + front) na VM Ubuntu Server. Roda uma vez;
# depois disso, o deploy de cada push no develop é feito pelo GitHub Actions
# (.github/workflows/deploy.yml nos dois repositórios).
#
# Pré-requisitos já instalados na VM: git, nginx, Docker, Node 22 e Ollama
# com os modelos bge-m3 e llama3.2 (ver deploy/README.md).
#
# Rodar como root, desligado do terminal (o SSH pode cair sem parar o script):
#   sudo systemd-run --unit=instalar-akavision --collect --setenv=HOME=/root bash instalar-vm.sh
#   journalctl -u instalar-akavision -f
#
# Pode ser rodado de novo: pula o que já existe e nunca troca o .env.
set -euo pipefail
passo() { echo; echo "==> $*"; }
[ "$(id -u)" -eq 0 ] || { echo "Rode como root (sudo)."; exit 1; }

BASE=/opt/akavision
BACK=$BASE/backend
FRONT=$BASE/frontend
WWW=/var/www/akavision
UPLOADS=$BASE/uploads/documentos
IP=$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1)}')
como_deploy() { sudo -u deploy -H bash -c "$1"; }

passo "Usuário deploy (roda o backend e o runner do GitHub)"
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
passwd -l deploy >/dev/null
usermod -aG systemd-journal deploy  # para o deploy.yml mostrar o log em caso de erro
install -d -o deploy -g deploy "$BASE" "$BASE/uploads" "$UPLOADS" "$WWW"

passo "Código do GitHub (branch develop)"
[ -d "$BACK/.git" ] || como_deploy "git clone -b develop https://github.com/DenariusData/API-6SEM-BACKEND.git $BACK"
[ -d "$FRONT/.git" ] || como_deploy "git clone -b develop https://github.com/DenariusData/API-6SEM-FRONTEND.git $FRONT"
como_deploy "git -C $BACK pull --ff-only && git -C $FRONT pull --ff-only"

passo ".env do backend (senhas geradas aqui, nunca vão para o git)"
if [ ! -f "$BACK/.env" ]; then
  PW_RAG=$(openssl rand -hex 24)
  PW_CRED=$(openssl rand -hex 24)
  cat > "$BACK/.env" <<ENV
DEBUG=False
ALLOWED_HOSTS=$IP,localhost,127.0.0.1
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
OLLAMA_URL=http://localhost:11434
PROCESSAMENTO_AUTOMATICO=true
DOCUMENTOS_UPLOAD_DIR=$UPLOADS
FRONTEND_URL=http://$IP
ENV
  chown deploy:deploy "$BACK/.env"
  chmod 600 "$BACK/.env"
fi

passo "Bancos (Docker, só em 127.0.0.1)"
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
[ -x /home/deploy/.local/bin/uv ] || como_deploy "curl -LsSf https://astral.sh/uv/install.sh | sh"
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

passo "Build do front"
como_deploy "cd $FRONT && npm ci && echo 'VITE_API_BASE_URL=/' > .env.production.local && npm run build && rsync -a --delete dist/ $WWW/"

passo "Site no nginx"
install -m 644 "$BACK/deploy/nginx-akavision.conf" /etc/nginx/sites-available/akavision
ln -sf /etc/nginx/sites-available/akavision /etc/nginx/sites-enabled/akavision
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

passo "Conferência"
sleep 3
systemctl is-active akavision-backend nginx ollama docker || true
docker ps --format '{{.Names}}: {{.Status}}' || true
curl -s -o /dev/null -w 'API: HTTP %{http_code} (401 = no ar)\n' http://127.0.0.1:8000/api/auth/me/ || true
curl -s -o /dev/null -w 'Site: HTTP %{http_code} (200 = no ar)\n' http://127.0.0.1/ || true
echo
echo "PRONTO. Abra http://$IP no navegador de qualquer PC da rede."
