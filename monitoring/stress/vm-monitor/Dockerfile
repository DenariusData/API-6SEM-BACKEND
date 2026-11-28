# VM de monitoramento em um container: Prometheus, Alertmanager, blackbox e
# Grafana dividindo os mesmos 512 MB / 1 vCPU. Contexto: a pasta monitoring/.
FROM prom/prometheus:v3.5.0 AS prometheus
FROM prom/alertmanager:v0.28.1 AS alertmanager
FROM prom/blackbox-exporter:v0.27.0 AS blackbox

FROM grafana/grafana:12.1.1
USER root
COPY --from=prometheus /bin/prometheus /bin/promtool /usr/local/bin/
COPY --from=alertmanager /bin/alertmanager /usr/local/bin/
COPY --from=blackbox /bin/blackbox_exporter /usr/local/bin/

COPY stress/vm-monitor/prometheus.yml /etc/prometheus/prometheus.yml
COPY prometheus/alertas.yml /etc/prometheus/alertas.yml
COPY prometheus/alertas-vms.yml /etc/prometheus/alertas-vms.yml
COPY alertmanager/alertmanager.yml /etc/alertmanager/alertmanager.yml
COPY blackbox/blackbox.yml /etc/blackbox/blackbox.yml
COPY stress/vm-monitor/grafana/datasources.yml /etc/grafana/provisioning/datasources/datasources.yml
COPY grafana/provisioning/dashboards/dashboards.yml /etc/grafana/provisioning/dashboards/dashboards.yml
COPY grafana/dashboards /var/lib/grafana/dashboards
COPY stress/vm-monitor/entrypoint.sh /entrypoint-monitor.sh
RUN sed -i 's/\r$//' /entrypoint-monitor.sh && chmod +x /entrypoint-monitor.sh \
 && promtool check config /etc/prometheus/prometheus.yml

ENV GF_SECURITY_ADMIN_USER=admin GF_SECURITY_ADMIN_PASSWORD=admin \
    GF_AUTH_ANONYMOUS_ENABLED=true GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/akavision.json
EXPOSE 3000 9090 9093
ENTRYPOINT ["/entrypoint-monitor.sh"]
