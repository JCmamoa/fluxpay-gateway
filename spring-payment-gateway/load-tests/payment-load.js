import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  scenarios: {
    carga_normal: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },
        { duration: '1m', target: 50 },
        { duration: '30s', target: 0 },
      ],
    },
    teste_estresse: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 200,
      maxVUs: 500,
      stages: [
        { duration: '20s', target: 50 },
        { duration: '20s', target: 200 },
        { duration: '20s', target: 400 },
        { duration: '20s', target: 0 },
      ],
      startTime: '2m30s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'],
    http_req_failed: ['rate<0.3'],
  },
};

const BASE_URL = 'http://localhost:8080';
const USERNAME = __ENV.AUTH_USERNAME;
const PASSWORD = __ENV.AUTH_PASSWORD;

export function setup() {
  if (!USERNAME || !PASSWORD) {
    throw new Error('Set AUTH_USERNAME and AUTH_PASSWORD for the local test application.');
  }

  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ username: USERNAME, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  const token = res.json('access_token');

  check(res, {
    'login successful': (response) => response.status === 200 && Boolean(token),
  });

  if (res.status !== 200 || !token) {
    throw new Error(`Local test login failed with status ${res.status}.`);
  }

  return { token };
}

export default function (data) {
  const payload = JSON.stringify({
    payerId: `payer-${__VU}`,
    payeeId: `payee-${__ITER}`,
    amount: Number((Math.random() * 500 + 10).toFixed(2)),
    currency: 'BRL',
  });

  const res = http.post(`${BASE_URL}/api/payments`, payload, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${data.token}`,
    },
  });

  check(res, {
    'status 201 ou 503': (response) => response.status === 201 || response.status === 503,
    'resposta em menos de 6s': (response) => response.timings.duration < 6000,
  });

  sleep(Math.random() * 0.5);
}
