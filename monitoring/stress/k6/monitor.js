// Stress da VM de monitoramento (Prometheus + Alertmanager + Grafana +
// blackbox), 512 MB / 1 vCPU. Cada usuário virtual é uma pessoa com o painel
// do Grafana aberto: a cada 15 s (refresh do painel) o navegador pede de novo
// as consultas dos gráficos da última 1 h, pelo Grafana até o Prometheus.
// Enquanto isso o Prometheus continua coletando as outras VMs e avaliando
// os alertas.
import http from 'k6/http';
import { check, sleep } from 'k6';

const GRAFANA = 'http://10.10.10.8:3000';
const DS = `${GRAFANA}/api/datasources/proxy/uid/prometheus/api/v1`;

const CONSULTAS = [
  'sum(rate(django_http_requests_total_by_view_transport_method_total{view!="prometheus-django-metrics"}[1m])) * 60 or vector(0)',
  'akavision:erros_5xx:proporcao_5m',
  'sum by (status) (rate(django_http_responses_total_by_status_view_method_total{view!="prometheus-django-metrics"}[5m])) * 60',
  'histogram_quantile(0.95, sum by (le, view) (rate(django_http_requests_latency_seconds_by_view_method_bucket{view!="prometheus-django-metrics"}[5m])))',
  'akavision:latencia_p95_rotas_comuns:5m',
  'histogram_quantile(0.95, sum by (le, operacao) (rate(akavision_ollama_duracao_segundos_bucket[5m])))',
  'akavision:falhas_login:10m',
  'sum by (view, status) (increase(django_http_responses_total_by_status_view_method_total{status=~"401|403|423"}[5m]))',
  'max(probe_success) by (alvo)',
  'probe_duration_seconds',
  'count(ALERTS{alertstate="firing"}) or vector(0)',
  'rate(process_cpu_seconds_total[1m])',
];

export const options = {
  scenarios: {
    paineis: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '60s', target: 5 },
        { duration: '60s', target: 20 },
        { duration: '60s', target: 50 },
        { duration: '60s', target: 100 },
        { duration: '60s', target: 200 },
        { duration: '20s', target: 0 },
      ],
      gracefulRampDown: '20s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const fim = Math.floor(Date.now() / 1000);
  const inicio = fim - 3600;
  http.get(`${GRAFANA}/api/dashboards/uid/akavision`, { tags: { rota: 'dashboard' }, timeout: '60s' });
  const lote = CONSULTAS.map((q) => [
    'GET',
    `${DS}/query_range?query=${encodeURIComponent(q)}&start=${inicio}&end=${fim}&step=15`,
    null,
    { tags: { rota: 'query_range' }, timeout: '60s' },
  ]);
  http.batch(lote).forEach((r) => check(r, { 'consulta 200': (x) => x.status === 200 }));
  sleep(15);
}
