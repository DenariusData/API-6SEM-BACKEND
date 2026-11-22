#!/usr/bin/env bash
# Prepara um clone da VM akavision (VirtualBox) para virar outra VM: nome e
# identidade novos e sem o que veio copiado da original e não é do papel dele.
#
# Rodar UMA vez no clone, ainda na rede host-only do VirtualBox (sem
# internet), antes de ligar o clone na rede das VMs (deploy/README.md):
#   sudo bash preparar-clone.sh --nome akavision-ia --papel ia --mac 08:00:27:aa:bb:cc
#
# --mac é a trava de segurança: o script só roda na VM que tem esse MAC (o
# do clone, que o VirtualBox mostra), para nunca apagar nada da original.
# No fim a VM desliga, pronta para ligar a placa de rede na Rede NAT
# "akavision-net" (deploy/README.md).
#
# O que faz:
# - nome da máquina, machine-id e chaves SSH novos: sem isso, o DHCP e o
#   SSH enxergam o clone como se fosse a original (mesmo IP, mesma chave);
# - tira o runner do GitHub copiado da original. Só apaga os arquivos aqui:
#   o registro no GitHub é o da original e continua valendo para ela;
# - apaga os dados do back que vieram na cópia (bancos com dados pessoais,
#   .env com as senhas, PDFs enviados). Front e IA não guardam nada disso.
set -euo pipefail
passo() { echo; echo "==> $*"; }
erro() { echo "ERRO: $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || erro "rode como root (sudo)."

NOME="" PAPEL="" MAC=""
while [[ $# -gt 0 ]]; do
  [[ $# -ge 2 ]] || erro "$1 precisa de um valor depois dele."
  case "$1" in
    --nome) NOME=$2 ;;
    --papel) PAPEL=$2 ;;
    --mac) MAC=$(echo "$2" | tr 'A-F' 'a-f') ;;
    *) erro "opção desconhecida: $1" ;;
  esac
  shift 2
done
[[ $NOME =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || erro "--nome: use letras minúsculas, números e hífen (ex.: akavision-ia)."
[[ $PAPEL =~ ^(front|ia)$ ]] || erro "--papel: use front ou ia (o back fica na VM original)."
[[ $MAC =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] || erro "--mac: use o formato 08:00:27:aa:bb:cc."

if ! ip -o link | grep -q "link/ether $MAC "; then
  erro "esta VM não tem o MAC $MAC. Nada foi alterado. Confira se está no clone certo."
fi
echo "Clone confirmado pelo MAC $MAC. Hoje: $(hostname) -> vai virar: $NOME ($PAPEL)"

passo "Nome da máquina: $NOME"
hostnamectl set-hostname "$NOME"
if grep -q '^127\.0\.1\.1' /etc/hosts; then
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1 $NOME/" /etc/hosts
else
  echo "127.0.1.1 $NOME" >> /etc/hosts
fi

passo "machine-id novo (o DHCP passa a ver outra máquina)"
rm -f /etc/machine-id /var/lib/dbus/machine-id
systemd-machine-id-setup
if [[ -d /var/lib/dbus ]]; then ln -sf /etc/machine-id /var/lib/dbus/machine-id; fi
rm -f /var/lib/systemd/network/*lease* /var/lib/dhcp/*.leases 2>/dev/null || true

passo "Chaves SSH novas desta VM"
rm -f /etc/ssh/ssh_host_*
ssh-keygen -A

passo "Runner do GitHub copiado da original (removido só aqui)"
for unidade in /etc/systemd/system/actions.runner.*.service; do
  [[ -e $unidade ]] || continue
  pasta=$(sed -n 's/^WorkingDirectory=//p' "$unidade")
  systemctl disable --now "$(basename "$unidade")" >/dev/null 2>&1 || true
  rm -f "$unidade"
  if [[ -n $pasta ]]; then
    # .runner*, .credentials*: as versões novas do runner também guardam o
    # registro em .runner_migrated e .credentials_migrated.
    rm -f "$pasta"/.runner* "$pasta"/.credentials* "$pasta/.service"
    if compgen -G "$pasta/.runner*" >/dev/null || compgen -G "$pasta/.credentials*" >/dev/null; then
      erro "o registro do runner copiado continua em $pasta. Apague os arquivos .runner* e .credentials* antes de seguir."
    fi
  fi
  echo "  $(basename "$unidade") removido"
done
systemctl daemon-reload

passo "Dados do back copiados da original (bancos, .env, PDFs)"
BACK=/opt/akavision/backend
if command -v docker >/dev/null; then
  systemctl start docker >/dev/null 2>&1 || true
  docker rm -f rag-postgres credenciais-postgres >/dev/null 2>&1 || true
  docker volume rm akavision_rag_pgdata akavision_credenciais_pgdata >/dev/null 2>&1 || true
  echo "  bancos apagados: $(docker volume ls -q | grep -c pgdata || true) volume(s) de banco restante(s)"
fi
systemctl disable --now akavision-backend >/dev/null 2>&1 || true
rm -f /etc/systemd/system/akavision-backend.service /etc/sudoers.d/akavision-deploy
rm -f "$BACK/.env"
rm -rf /opt/akavision/uploads
systemctl daemon-reload

if [[ $PAPEL == ia ]]; then
  passo "VM da IA: sem código, site nem usuário deploy"
  rm -rf /opt/akavision /var/www/akavision
  rm -f /etc/nginx/sites-enabled/akavision /etc/nginx/sites-available/akavision
  if id deploy >/dev/null 2>&1; then
    pkill -u deploy >/dev/null 2>&1 || true
    userdel -r deploy >/dev/null 2>&1 || true
  fi
fi

passo "Pronto. Desligando: ligue a placa de rede na Rede NAT akavision-net e ligue de novo."
sync
systemctl poweroff
