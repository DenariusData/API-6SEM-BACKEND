// Stress da VM do back (gunicorn 3x4 + Postgres rag + Postgres credenciais),
// 512 MB / 1 vCPU. Bate direto no gunicorn (:8000), como o nginx do front.
//
// Fase 1 "navegacao" (0-5 min): usuários logados usando as telas comuns
//   (me, listas de apoio, acervo, meus documentos, auditoria).
// Fase 2 "login" (5:45-9:15): rajadas de login, a rota mais cara em CPU
//   (a senha passa pelo PBKDF2 do Django, lento de propósito).
// A rota da pergunta (IA) fica de fora: ela é medida no teste da VM da IA.
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = 'http://10.10.10.5:8000/api';
const H = { 'Content-Type': 'application/json', Host: '10.10.10.5:8000' };

const USUARIOS = [
  { email: 'engenharia@akaer.com.br', password: 'eng123' },
  { email: 'qualidade@akaer.com.br', password: 'qual123' },
  { email: 'admin@akaer.com.br', password: 'admin123' },
];

export const options = {
  scenarios: {
    navegacao: {
      executor: 'ramping-vus',
      exec: 'navegacao',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },
        { duration: '45s', target: 10 },
        { duration: '30s', target: 30 },
        { duration: '45s', target: 30 },
        { duration: '30s', target: 60 },
        { duration: '45s', target: 60 },
        { duration: '30s', target: 120 },
        { duration: '45s', target: 120 },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '15s',
    },
    login: {
      executor: 'ramping-arrival-rate',
      exec: 'login',
      startTime: '5m45s',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 30,
      maxVUs: 120,
      stages: [
        { duration: '30s', target: 1 },
        { duration: '30s', target: 3 },
        { duration: '30s', target: 3 },
        { duration: '30s', target: 6 },
        { duration: '30s', target: 6 },
        { duration: '30s', target: 10 },
        { duration: '30s', target: 10 },
      ],
    },
  },
  thresholds: {
    'http_req_duration{scenario:navegacao}': ['p(95)<500'],
    'http_req_duration{scenario:login}': ['p(95)<2000'],
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function entrar(u) {
  return http.post(`${BASE}/auth/login/`, JSON.stringify(u), {
    headers: H,
    timeout: '60s',
    tags: { rota: 'login' },
  });
}

export function setup() {
  const tokens = USUARIOS.map((u) => {
    const r = entrar(u);
    if (r.status !== 200) throw new Error(`login de ${u.email} falhou: ${r.status} ${r.body}`);
    const token = r.json('token');
    // Desde o AKA-109 as telas exigem o aceite dos termos obrigatórios.
    if (r.json('termos_pendentes')) {
      const auth = { headers: { ...H, Authorization: `Bearer ${token}` } };
      const termos = http.get(`${BASE}/auth/termos-pendentes/`, auth).json('termos') || [];
      const respostas = termos.map((t) => ({ termo_id: t.id, aceito: true }));
      http.post(`${BASE}/auth/termos/aceite/`, JSON.stringify({ respostas }), auth);
    }
    return { token, admin: u.email.startsWith('admin') };
  });
  return { tokens };
}

const ROTAS = [
  'auth/me/',
  'tipos-documento/',
  'areas/',
  'niveis-sigilo/',
  'documents/disponiveis/',
  'documents/meus/',
];

export function navegacao(dados) {
  const t = dados.tokens[__VU % dados.tokens.length];
  const p = { headers: { ...H, Authorization: `Bearer ${t.token}` }, timeout: '60s' };
  for (const rota of ROTAS) {
    const r = http.get(`${BASE}/${rota}`, { ...p, tags: { rota } });
    check(r, { 'status 200': (x) => x.status === 200 });
  }
  if (t.admin) {
    const r = http.get(`${BASE}/auditoria/logs/`, { ...p, tags: { rota: 'auditoria/logs/' } });
    check(r, { 'auditoria 200': (x) => x.status === 200 });
  }
  sleep(1);
}

export function login() {
  const r = entrar(USUARIOS[Math.floor(Math.random() * 2)]);
  check(r, { 'login 200': (x) => x.status === 200 });
}

// Para um teste rápido: k6 run --vus 1 --iterations 1 back.js
export default function (dados) {
  navegacao(dados);
}
