#!/bin/sh
# Os quatro processos da VM de monitoramento. Se um cair, o container sai
# (como um serviço systemd que não volta), e o teste registra a falha.
set -e
mkdir -p /prometheus /alertmanager
prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=15d --web.enable-lifecycle &
alertmanager --config.file=/etc/alertmanager/alertmanager.yml --storage.path=/alertmanager &
blackbox_exporter --config.file=/etc/blackbox/blackbox.yml &
exec /run.sh
