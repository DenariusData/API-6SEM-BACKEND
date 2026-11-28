// Stress da VM da IA (só o Ollama), 512 MB / 1 vCPU. Bate direto no Ollama
// com as mesmas chamadas do pergunta.py: embedding da pergunta (bge-m3) e a
// resposta (llama3.2).
import http from 'k6/http';
import { check, sleep } from 'k6';

const OLLAMA = __ENV.OLLAMA || 'http://10.10.10.7:11434';
const PERGUNTA = 'Qual o intervalo de inspeção do trem de pouso descrito no manual?';
const TRECHO =
  'A inspeção do trem de pouso deve ser feita a cada 500 horas de voo ou 6 meses, o que ocorrer primeiro.';

export const options = {
  scenarios: {
    embedding: {
      executor: 'ramping-vus',
      exec: 'embedding',
      startVUs: 1,
      stages: [
        { duration: '60s', target: 1 },
        { duration: '60s', target: 2 },
        { duration: '60s', target: 4 },
      ],
      gracefulRampDown: '60s',
    },
    resposta: {
      executor: 'ramping-vus',
      exec: 'resposta',
      startTime: '3m30s',
      startVUs: 1,
      stages: [
        { duration: '90s', target: 1 },
        { duration: '90s', target: 3 },
        { duration: '90s', target: 6 },
      ],
      gracefulRampDown: '120s',
    },
  },
  thresholds: {
    'http_req_duration{scenario:resposta}': ['p(95)<45000'],
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const JSONH = { headers: { 'Content-Type': 'application/json' } };

export function embedding() {
  const r = http.post(`${OLLAMA}/api/embed`, JSON.stringify({ model: 'bge-m3', input: PERGUNTA }), {
    ...JSONH,
    timeout: '300s',
    tags: { operacao: 'embed' },
  });
  check(r, { 'embed 200': (x) => x.status === 200 });
  if (r.status !== 200) {
    console.warn(`embed ${r.status}: ${String(r.body).slice(0, 200)}`);
    sleep(2); // falha rápida: não vira um laço de milhares de pedidos
  }
}

export function resposta() {
  const corpo = {
    model: 'llama3.2',
    stream: false,
    options: { num_predict: 96, num_ctx: 2048, temperature: 0 },
    messages: [
      { role: 'system', content: 'Responda em português, em até 3 frases, só com base no trecho.' },
      { role: 'user', content: `Trecho: ${TRECHO}\n\nPergunta: ${PERGUNTA}` },
    ],
  };
  const r = http.post(`${OLLAMA}/api/chat`, JSON.stringify(corpo), {
    ...JSONH,
    timeout: '600s',
    tags: { operacao: 'chat' },
  });
  check(r, { 'chat 200': (x) => x.status === 200 });
  if (r.status !== 200) {
    console.warn(`chat ${r.status}: ${String(r.body).slice(0, 200)}`);
    sleep(2);
  }
}
