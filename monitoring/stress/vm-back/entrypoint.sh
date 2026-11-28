#!/bin/bash
# Sobe a "VM do back": dois Postgres (mesmos parâmetros da VM), migrations,
# seeds de demonstração e o gunicorn com deploy/gunicorn.conf.py.
set -euo pipefail

PGBIN=/usr/lib/postgresql/17/bin

subir_banco() {
  local nome=$1 porta=$2 extra=$3
  local dir=/dados/pg-$nome
  mkdir -p "$dir" /dados/uploads
  chown -R postgres:postgres /dados
  if [ ! -s "$dir/PG_VERSION" ]; then
    echo "$nome" > /tmp/pw-$nome && chown postgres /tmp/pw-$nome
    su postgres -c "$PGBIN/initdb -D $dir -U $nome --auth-local=trust --auth-host=scram-sha-256 --pwfile=/tmp/pw-$nome" >/dev/null
  fi
  su postgres -c "$PGBIN/pg_ctl -D $dir -l /dados/pg-$nome.log -w start -o '-p $porta -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp $extra'"
}

# rag: igual ao deploy/docker-compose.prod.yml (shared_buffers e
# maintenance_work_mem vêm do compose do laboratório).
subir_banco rag 5434 "-c shared_buffers=${PG_RAG_SHARED_BUFFERS} -c maintenance_work_mem=${PG_RAG_MAINTENANCE_WORK_MEM}"
subir_banco credenciais 5433 ""

su postgres -c "psql -h /tmp -p 5434 -U rag -d postgres -tc \"select 1 from pg_database where datname='rag'\" | grep -q 1 || createdb -h /tmp -p 5434 -U rag rag"
su postgres -c "psql -h /tmp -p 5433 -U credenciais -d postgres -tc \"select 1 from pg_database where datname='credenciais'\" | grep -q 1 || createdb -h /tmp -p 5433 -U credenciais credenciais"
su postgres -c "psql -h /tmp -p 5434 -U rag -d rag -c 'create extension if not exists vector'" >/dev/null

cd /app
python manage.py migrate --noinput >/dev/null
python manage.py migrate --database credenciais_db --noinput >/dev/null
python manage.py seed_taxonomia >/dev/null
python manage.py seed_usuarios_demo >/dev/null || true
echo "vm-back pronta"

exec gunicorn api.wsgi:application --config deploy/gunicorn.conf.py
