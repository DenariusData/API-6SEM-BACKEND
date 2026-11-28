// Stress da VM do front (nginx com o build do Vue), 512 MB / 1 vCPU.
// Cada usuário virtual abre a página inteira (HTML + JS + CSS, com gzip),
// espera 1 s e abre de novo. A carga sobe em degraus até quebrar.
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = 'http://10.10.10.6';

export const options = {
  scenarios: {
    pagina: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 25 },
        { duration: '60s', target: 25 },
        { duration: '30s', target: 100 },
        { duration: '60s', target: 100 },
        { duration: '30s', target: 300 },
        { duration: '60s', target: 300 },
        { duration: '30s', target: 600 },
        { duration: '60s', target: 600 },
        { duration: '20s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  const html = http.get(`${BASE}/`).body || '';
  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
  return { assets };
}

const GZIP = { headers: { 'Accept-Encoding': 'gzip' }, timeout: '30s' };

export default function (dados) {
  const r = http.get(`${BASE}/`, GZIP);
  check(r, { 'index 200': (x) => x.status === 200 });
  const lote = dados.assets.map((a) => ['GET', `${BASE}${a}`, null, GZIP]);
  if (lote.length) http.batch(lote);
  sleep(1);
}
