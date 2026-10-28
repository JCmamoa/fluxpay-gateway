# FluxPay — Gateway de Pagamento + CRM/Dashboard (v0.3)

Gateway de pagamentos local para **PIX**, **cartão de crédito** e **cartão de débito**, com criação
idempotente, processamento assíncrono em fila durável, histórico imutável e webhooks assinados com
retry, backoff e DLQ — agora com a extensão **v0.3**: hub de eventos com **WebSocket em tempo
real**, **CRM** (360°, tags, notas, segmentos), **dashboard ao vivo** (Chart.js vendored),
**automações**, **busca RAG** e **agente IA** com confirmação em 2 passos.

O projeto foi construído a partir das especificações técnicas aprovadas (`specs/payment-gateway.md`,
v0.2, e `specs/crm-dashboard.md`, v0.3) e roda no Windows com **um único comando**, nativamente ou
via Docker Compose. Todos os provedores são simulados; nenhum adquirente real é contatado.

| Item | Valor |
| --- | --- |
| Versão | 1.0.0 (gateway v0.2 + extensão v0.3) |
| API | `http://localhost:8000` |
| Console de demonstração | `http://localhost:8000/` |
| Documentação interativa | `http://localhost:8000/docs` |
| Healthcheck | `http://localhost:8000/health` |
| WebSocket | `ws://localhost:8000/ws/events?token=<api_key>` |
| Moeda | BRL, sempre em centavos (inteiro) |

---

## 1. Stack e decisões

| Camada | Tecnologia |
| --- | --- |
| Runtime | Python 3.12 (compatível com 3.11+) |
| API | FastAPI 0.115 + Uvicorn 0.34 |
| Validação | Pydantic 2.10 |
| Banco / fila / outbox | SQLite 3 com WAL, transações `BEGIN IMMEDIATE` e schema versionado |
| Worker e dispatcher | Threads independentes no mesmo processo, com leases e recuperação após restart |
| HTTP client | httpx 0.28 (webhooks) |
| PIX | BR Code EMV (copia-e-cola) + QR SVG via `segno` |
| Console | HTML + CSS + JavaScript puro, servido pela própria API; 6 abas, tema claro/escuro |
| Realtime | WebSocket nativo do Uvicorn + broadcaster em thread; Chart.js 4.5.0 vendored (sem CDN) |
| Testes | pytest 8.3 |
| Empacotamento | Docker multi-stage, non-root (UID 10001), healthcheck e volume nomeado |

**Por que SQLite?** A especificação permite fila persistida em banco e exige zero dependências
externas obrigatórias. SQLite em modo WAL entrega durabilidade, serialização correta de escrita e
operação com um único comando. A aplicação é single-node por design; para escala horizontal seria
necessário trocar o adaptador de banco/fila.

### Arquitetura

```text
Merchant / Console
       │ HTTP + Bearer + Idempotency-Key
       ▼
FastAPI (auth, validação, idempotência, console)
       │ transação BEGIN IMMEDIATE
       ▼
SQLite ── payments / idempotency_keys / payment_events
       │              └── outbox: webhook_deliveries
       │ fila durável: queue_messages
       ▼
Payment worker (CAS pending → processing → succeeded|failed)
       │ evento + outbox na mesma transação
       ▼
Webhook dispatcher (HMAC + retry/backoff + DLQ)
       │ HTTPS POST
       ▼
Endpoint do merchant (receiver demo incluso)
```

Garantias principais:

- **At-least-once** em toda a cadeia; efeitos **exactly-once** via chave de idempotência e CAS.
- Webhook criado na **mesma transação** da mudança de status (transactional outbox).
- Mensagens e entregas sobrevivem a restart; leases vencidos são recuperados.
- `payment_events` e `webhook_attempts` são **append-only**, reforçados por triggers no banco.
- PAN e CVV nunca são persistidos nem logados; apenas bandeira e últimos 4 dígitos.

---

## 2. Execução nativa no Windows (recomendada para o demo)

### Pré-requisitos

- Windows 10/11 com PowerShell 5.1+
- Python 3.11 ou superior (recomendado 3.12)

Se necessário:

```powershell
winget install -e --id Python.Python.3.12
```

> Evite o alias `python` da Microsoft Store quando ele não estiver configurado. O `run.ps1`
> localiza automaticamente o `py` launcher, o `PATH` ou instalações em
> `%LOCALAPPDATA%\Programs\Python\Python3*`.

### Um comando

```powershell
cd app_build
.\run.ps1
```

O script:

1. encontra um Python 3.11+;
2. cria `.venv` na primeira execução;
3. instala `requirements.txt`;
4. copia `.env.example` para `.env` se necessário;
5. sobe API + worker + dispatcher com `python -m app`.

Acesse:

- Console: <http://localhost:8000/>
- Docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

Opções:

```powershell
.\run.ps1 -Port 8080        # usa outra porta e ajusta o receiver de webhook
.\run.ps1 -SkipInstall      # não reinstala dependências
.\run.ps1 -Reinstall        # força reinstalação
```

> `-Port` ajusta automaticamente `PORT` e `WEBHOOK_URL`. Se alterar `PORT` manualmente no `.env`,
> ajuste `WEBHOOK_URL` para a mesma porta, senão o receiver demo não receberá as entregas.

Se a política de execução bloquear o script:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

O `run.ps1` não funciona em ambientes não-Windows. Nesses casos use os comandos manuais abaixo.

### Execução manual (Windows, Linux ou macOS)

```powershell
cd app_build
py -3.12 -m venv .venv        # ou: python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1
# Linux/macOS
# source .venv/bin/activate

python -m pip install -r requirements.txt
Copy-Item .env.example .env      # Linux/macOS: cp .env.example .env
python -m app
```

O servidor lê `HOST` e `PORT` do ambiente/.env. O banco é criado automaticamente em
`./data/gateway.db` (ou no caminho definido por `DATABASE_PATH`).

### Testes

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

A suíte cobre idempotência concorrente com servidor HTTP real, máquina de estados/CAS, PIX,
webhooks (assinatura, retry, DLQ) e contratos da API. Veja a seção 10 para o resultado da última
execução.

---

## 3. Execução com Docker

Pré-requisito: Docker Desktop com o engine Linux em execução.

```powershell
cd app_build
Copy-Item .env.example .env   # opcional; o Compose também usa os defaults
docker compose up --build
```

Console: <http://localhost:8000/> (ou `${PORT}` definido no `.env`).

O Compose:

- publica `${PORT:-8000}` no host;
- injeta API key, segredo de webhook, porta e caminho do banco via ambiente/`.env`;
- usa o volume nomeado **`fluxpay_gateway_data`** em `/app/data`;
- define `restart: unless-stopped`;
- executa healthcheck em `/health`;
- mantém o receiver de webhook dentro do container (`127.0.0.1:8000`).

Persistência entre restarts:

```powershell
docker compose restart          # dados preservados
docker compose down             # dados preservados no volume
docker compose down -v          # apaga o volume (reset completo)
```

Observações:

- O `.dockerignore` exclui `.env`, `.venv`, `data/`, testes, caches e bancos locais.
- A imagem final não contém segredos: tudo vem do ambiente.
- O processo roda como `fluxpay` (UID/GID 10001), nunca como root.
- A porta interna do container é sempre `8000`; alterar `PORT` muda apenas o mapeamento do host.
- Em Docker, mantenha `WEBHOOK_URL` apontando para `127.0.0.1:8000` (loopback interno), mesmo que
  `PORT` do host seja outra. O receiver demo roda no mesmo container.

> **Status de verificação:** os arquivos Docker foram revisados estaticamente, mas o build e o
> `docker compose up` **não puderam ser executados neste ambiente** porque o Docker CLI/engine não
> está instalado. A execução nativa foi validada de ponta a ponta.

---

## 4. Credenciais do demo

| Campo | Valor |
| --- | --- |
| Merchant ID | `mch_demo` |
| Nome | `Loja Demo` |
| API key | `sk_demo_fluxpay_2026` |
| Webhook secret | `whsec_demo_fluxpay_2026` |
| Endpoint | `whe_demo` |
| URL do receiver | `http://127.0.0.1:8000/demo/webhook-receiver?endpoint=whe_demo` |
| Chave PIX | `demo@fluxpay.local` |
| Cidade PIX | `SAO PAULO` |

O console preenche a API key automaticamente porque `DEMO_EXPOSE_API_KEY=true`. Em produção esse
valor **deve** ser `false` (a aplicação recusa iniciar com `APP_ENV=production` e flags de demo
ativas).

> Essas credenciais são públicas e exclusivas para demonstração local. Rotacione antes de qualquer
> ambiente compartilhado.

---

## 5. Console de demonstração

### 5.1 Gateway

Abra <http://localhost:8000/> e use a aba **Gateway**. O console permite:

- criar pagamentos PIX, crédito e débito;
- filtrar e paginar cobranças;
- abrir o detalhe com linha do tempo de eventos;
- exibir QR Code, copia-e-cola e validade do PIX;
- simular o pagamento de um PIX (`processing` → `succeeded`);
- inspecionar entregas, tentativas, corpo assinado e DLQ;
- reinjetar entregas da DLQ;
- injetar falhas no receiver para demonstrar retry/backoff;
- visualizar métricas operacionais.

O console usa a própria API com Bearer token; nenhum segredo adicional é necessário no demo.

Fluxo sugerido:

1. Crie um PIX de `R$ 129,90`.
2. No detalhe, copie o código PIX ou escaneie o QR.
3. Clique em **Simular pagamento**.
4. Acompanhe `pending → processing → succeeded` e o receipt do webhook.
5. Use **Injetar falhas** (1–20) e crie outro PIX para ver as tentativas e o backoff.
6. Abra **Webhooks → DLQ** para inspecionar e reprocessar entregas esgotadas.

### 5.2 Abas v0.3 (Dashboard, CRM, Automações, Assistente, Dados)

O console v0.3 tem navegação por 6 abas e tema claro/escuro persistido (`localStorage`):

| Aba | Conteúdo |
| --- | --- |
| **Gateway** | Painel de cobranças (v0.2, descrito acima) |
| **Dashboard** | KPIs (hoje/7d/30d, fila, DLQ) + gráficos Chart.js (linha de volume, rosca por método, barras por status) atualizados em tempo real via WS |
| **CRM** | Tabela de clientes com tags, busca, view 360° (pagamentos, eventos, notas), criação de notas e tags, segmentos calculados |
| **Automações** | Lista/cria/edita/exclui regras (`event_type → condições → ação`), com feed de alertas disparados |
| **Assistente** | Chat com o agente IA: respostas com fontes (RAG) e confirmação em 2 passos para ações sensíveis |
| **Dados** | Tabela de rollup diário, download de CSV (`payments`, `customers`) e feed de eventos processados |

Demo de ponta a ponta (v0.3):

1. Abra o **Dashboard** — o indicador **WS** no topo deve ficar verde.
2. Crie e simule um PIX no **Gateway**; a curva do dashboard reage em ≤ 1 s.
3. No **CRM**, abra o cliente recém-criado, adicione uma nota e uma tag.
4. Em **Automações**, veja a regra semeada (`webhook falha → alerta no console`) e os alertas
   gerados quando uma entrega esgota as tentativas.
5. No **Assistente**, pergunte `"Quero um resumo de pagamentos de hoje"` e depois
   `"reenvia o webhook do pagamento pay_..."` — a ação sensível exibe confirmação em 2 passos
   (retry de webhook) ou uma nota com confirmação (`"adiciona nota 'VIP' ao cliente ana@exemplo.com"`).
6. Em **Dados**, baixe o CSV de pagamentos.

---

## 6. API

Base: `http://localhost:8000`

Autenticação em todas as rotas `/v1/*`:

```http
Authorization: Bearer sk_demo_fluxpay_2026
```

Erros seguem o envelope estável:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": [{ "loc": ["body", "amount"], "msg": "Input should be greater than 0", "type": "greater_than" }]
  }
}
```

### 6.1 Criar pagamento — `POST /v1/payments`

Headers obrigatórios:

| Header | Regra |
| --- | --- |
| `Authorization` | `Bearer <api_key>` |
| `Idempotency-Key` | 1–255 caracteres, obrigatório, único por merchant |
| `Content-Type` | `application/json` |

Corpo:

```json
{
  "amount": 12990,
  "currency": "BRL",
  "method": "pix",
  "description": "Pedido #123",
  "customer": { "name": "Ana Silva", "email": "ana@exemplo.com" },
  "metadata": { "order_id": "ord_123" }
}
```

Regras:

- `amount` é inteiro em centavos, `> 0` e `<= 100000000` (R$ 1.000.000,00).
- `currency` aceita apenas `BRL`.
- `method` ∈ `pix`, `credit_card`, `debit_card`.
- `card` é obrigatório para cartões e proibido para PIX.
- Campos desconhecidos são rejeitados com `422` (`extra="forbid"`).
- `customer` e `metadata` são opcionais; metadata aceita até 30 chaves e 4 KiB serializados.

Resposta `202 Accepted` (criação):

```json
{
  "id": "pay_01J...",
  "object": "payment",
  "status": "pending",
  "method": "pix",
  "amount": 12990,
  "currency": "BRL",
  "description": "Pedido #123",
  "customer": { "name": "Ana Silva", "email": "ana@exemplo.com" },
  "metadata": { "order_id": "ord_123" },
  "authorization_code": null,
  "failure_reason": null,
  "created_at": "2026-10-26T12:00:00Z",
  "updated_at": "2026-10-26T12:00:00Z",
  "pix": {
    "txid": "A1B2C3D4E5F6...",
    "qr_code": "00020101021226...6304ABCD",
    "expires_at": "2026-10-26T12:30:00Z",
    "qr_code_image": "data:image/svg+xml;base64,..."
  }
}
```

`pix.qr_code` é o BR Code EMV (copia-e-cola). `pix.qr_code_image` é um SVG em data URI para
pré-visualização; o payload textual é a fonte de verdade.

#### Exemplo PIX (PowerShell)

```powershell
$headers = @{
  Authorization     = "Bearer sk_demo_fluxpay_2026"
  "Idempotency-Key" = "demo-pix-0001"
}

$body = @{
  amount      = 12990
  currency    = "BRL"
  method      = "pix"
  description = "Pedido #123"
  customer    = @{ name = "Ana Silva"; email = "ana@exemplo.com" }
  metadata    = @{ order_id = "ord_123" }
} | ConvertTo-Json -Depth 5

$payment = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/payments `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body

$payment.id
$payment.pix.txid
$payment.pix.qr_code
$payment.pix.expires_at
```

#### Exemplo cartão (PowerShell)

```powershell
$headers = @{
  Authorization     = "Bearer sk_demo_fluxpay_2026"
  "Idempotency-Key" = "demo-card-0001"
}

$body = @{
  amount   = 4590
  currency = "BRL"
  method   = "credit_card"
  customer = @{ name = "Ana Silva"; email = "ana@exemplo.com" }
  card     = @{
    number    = "4242424242424242"
    holder    = "ANA SILVA"
    exp_month = 12
    exp_year  = 2030
    cvv       = "123"
  }
} | ConvertTo-Json -Depth 5

$payment = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/payments `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body

$payment.status   # pending — o worker decide de forma assíncrona
```

O cartão retorna imediatamente com `pending`; o worker simula a autorização e a resposta final chega
por webhook e em `GET /v1/payments/{id}`.

#### Idempotência

- Mesma key + mesmo payload normalizado → `200 OK`, corpo original e `Idempotent-Replay: true`.
- Mesma key + payload diferente → `409 idempotency_conflict`, sem novo pagamento.
- Requisições simultâneas com a mesma key são serializadas por `BEGIN IMMEDIATE` + chave primária
  composta; apenas uma cria o pagamento.
- A fingerprint SHA-256 inclui os dados do cartão, mas o PAN/CVV nunca são persistidos. Para replay,
  reenvie o mesmo payload.
- Falhas de validação (`422`) e autenticação (`401`) não gravam a key: corrija e reutilize-a.
- Keys não expiram na v1.

### 6.2 Consultar — `GET /v1/payments/{id}`

Retorna o objeto completo do merchant autenticado. Outro merchant recebe `404 payment_not_found`
(não vaza existência).

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/v1/payments/$payment.id `
  -Headers @{ Authorization = "Bearer sk_demo_fluxpay_2026" }
```

### 6.3 Listar — `GET /v1/payments`

```http
GET /v1/payments?limit=20&starting_after=pay_...
```

- `limit`: 1–100 (default 20)
- `starting_after`: ID do último item da página anterior
- Resposta: `{ object, data, has_more, next_cursor }`
- Ordenação: `created_at` descendente, com desempate por ID
- Cursor inválido → `422 invalid_cursor`

### 6.4 Simular PIX pago — `POST /v1/payments/{id}/simulate-payment`

Representa o callback do provedor PIX. Disponível apenas com `DEMO_MODE=true`.

```powershell
$sim = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/payments/$payment.id/simulate-payment `
  -Headers @{ Authorization = "Bearer sk_demo_fluxpay_2026" } `
  -ContentType "application/json" `
  -Body '{"result":"paid"}'

$sim.status   # processing
```

A resposta é `202` com `{ id, object, status: "processing" }`. O worker conclui em seguida
(`succeeded`) e dispara o webhook.

Erros:

- `404` pagamento inexistente ou de outro merchant;
- `409 payment_not_simulatable` para cartões ou pagamento já terminal;
- `409 payment_expired` quando o QR venceu;
- `404` quando o demo está desabilitado.

### 6.5 Eventos — `GET /v1/payments/{id}/events`

Histórico imutável de auditoria, em ordem cronológica:

```json
{
  "object": "list",
  "data": [
    {
      "id": "evt_...",
      "object": "payment_event",
      "payment_id": "pay_...",
      "type": "payment.pending",
      "payload": { "from_status": null, "to_status": "pending", "reason": null, "webhook_enqueued": true },
      "created_at": "2026-10-26T12:00:00Z"
    }
  ]
}
```

### 6.6 Identidade e métricas

| Rota | Descrição |
| --- | --- |
| `GET /v1/me` | Merchant autenticado (`id`, `name`, `created_at`) |
| `GET /v1/metrics` | Contadores de pagamentos, fila e webhooks, uptime e versão |
| `GET /health` | Liveness/readiness; `503` se o banco falhar; inclui fila e contadores de entrega |

### 6.7 Webhook endpoints e entregas

| Rota | Descrição |
| --- | --- |
| `GET /v1/webhook-endpoints` | Lista endpoints do merchant |
| `PATCH /v1/webhook-endpoints/{id}` | Atualiza `url` e/ou `active` |
| `GET /v1/webhook-deliveries` | Lista entregas; filtros `status`, `payment_id`, `limit` |
| `GET /v1/webhook-deliveries/{id}` | Detalhe + log completo de tentativas |
| `POST /v1/webhook-deliveries/{id}/retry` | Reenfileira uma entrega (DLQ replay), resposta `202` |

`status` ∈ `pending`, `delivered`, `failed` (este último é a DLQ).

> O seed de boot reaplica `WEBHOOK_URL` e `WEBHOOK_SECRET` ao endpoint `whe_demo`; alterações feitas
> via `PATCH` são locais e voltam ao valor do ambiente no próximo restart.

### 6.8 Rotas demo

Disponíveis somente com `DEMO_MODE=true` (e não montadas quando `APP_ENV=production`):

| Rota | Descrição |
| --- | --- |
| `GET /demo/config` | Bootstrap do console; inclui a API key só se `DEMO_EXPOSE_API_KEY=true` |
| `POST /demo/webhook-receiver?endpoint=whe_demo` | Receiver local: valida HMAC, grava receipt e deduplica por `X-Event-Id` |
| `GET /demo/webhook-receiver/receipts` | Inspeciona o que o “merchant” recebeu |
| `POST /demo/webhook-failures` | `{"count": N}` faz as próximas N entregas responderem `503` |
| `POST /demo/reset-failures` | Zera a injeção de falhas |
| `GET /demo/echo` | Dica de assinatura + ID de evento de exemplo |

O receiver demo é um merchant simulado: ele rejeita assinaturas inválidas com `401` e responde `200`
para entregas válidas (ou `duplicate: true`).

### 6.9 Tabela de erros

| HTTP | Código | Quando |
| --- | --- | --- |
| `400` | `missing_idempotency_key` | Header ausente/vazio |
| `400` | `invalid_idempotency_key` | Key com mais de 255 caracteres |
| `401` | `invalid_api_key` | Bearer ausente, malformado ou inválido |
| `404` | `payment_not_found` | Pagamento inexistente ou de outro merchant |
| `404` | `not_found` | Rota inexistente / demo desabilitado |
| `409` | `idempotency_conflict` | Key reutilizada com payload diferente |
| `409` | `payment_not_simulatable` | Simulação inválida para o estado/método |
| `409` | `payment_expired` | QR PIX expirado |
| `413` | `payload_too_large` | Corpo acima de `MAX_BODY_BYTES` (default 256 KiB) |
| `422` | `validation_error` | Corpo/query inválidos; inclui detalhes sem ecoar PAN/CVV |
| `422` | `invalid_cursor` | `starting_after` inválido |
| `500` | `database_error` / `internal_error` | Falha interna, sem vazar stack trace |
| `404` | `customer_not_found` | Cliente inexistente (CRM/agent actions) |
| `404` | `segment_not_found` | Segmento inexistente |
| `404` | `automation_rule_not_found` | Regra de automação inexistente |
| `404` | `event_not_found` | `after` inexistente no hub de eventos |
| `404` | `confirmation_not_found` | Token de confirmação inexistente/expirado |
| `409` | `confirmation_already_used` | Token de confirmação já executado |
| `404` | `export_not_found` | Export inválido (`payments\|customers`) |

### 6.10 Rotas v0.3 — hub de eventos, CRM, dashboard, automações, dados, agente e RAG

Todas autenticadas com o mesmo Bearer. Prefixos: `/v1/*` e `WS /ws/events`.

#### Hub de eventos

| Rota | Descrição |
| --- | --- |
| `GET /v1/events` | Snapshot do hub (fonte de verdade para reconciliar o WS). Query: `after=<event_id>`, `type=payment.succeeded,crm.customer.created` (lista separada por vírgula), `limit` 1–500, `order=asc\|desc` |
| `WS /ws/events?token=<api_key>&after=<event_id>` | Broadcast em tempo real. Reconnect com `after` repete tudo após o watermark sem buracos nem duplicatas |

Contrato do WS:

- Autenticação no handshake: token inválido recusa com **close code `4401`**.
- `after` inexistente: **close code `4400`** com reason explicativa.
- Mensagens: `{"id": "evt_...", "type": "...", "created_at": "...", "data": {...}}`.
- Heartbeat: `{"id": null, "type": "server.ping", "data": {}}` a cada `WS_HEARTBEAT_SECONDS`
  (20 s default); o cliente usa para detectar conexão morta.
- Reconexão do cliente: tenta `after` do último evento recebido; se expirar/vazar, reconcilia com `GET /v1/events`.
- Retenção: `EVENT_STREAM_RETENTION_DAYS` (30). A passada de retenção apaga eventos antigos do hub.

#### CRM

| Rota | Descrição |
| --- | --- |
| `GET /v1/customers` | Lista clientes com agregados (total pago, nº pagamentos) e tags; filtros `q` (email/nome), `tag`, `limit`, `starting_after` |
| `GET /v1/customers/{id}` | View 360°: dados, tags, notas, pagamentos, últimos eventos, segmentos |
| `POST /v1/customers/{id}/notes` | Adiciona nota (body 1–2000, author 1–80) |
| `POST /v1/customers/{id}/tags` | Adiciona tag (normalizada para minúsculas, 1–40 chars) |
| `GET /v1/segments` | Lista segmentos declarativos com contagem atual |
| `POST /v1/segments` | Cria segmento. Definição (pelo menos 1 regra): `{"recency_max_days": 30, "frequency_min": 3, "method": "pix", "min_amount_cents": 10000}` — todos opcionais, combináveis |

A contagem é calculada na criação e reexecutada pelo job agendado (`JOBS_SEGMENTS_SECONDS`).

#### Automações

| Rota | Descrição |
| --- | --- |
| `GET /v1/automations/rules` | Lista regras (inclui seed `ar_seed_webhook_alert`) |
| `POST /v1/automations/rules` | Cria regra: `name`, `event_type` (exato ou curinga de namespace `payment.*`), `conditions` (máx 10), `action_type` ∈ `alerta_no_console` \| `tag_customer` \| `retry_webhook`, `action_config`, `enabled` |
| `GET /v1/automations/rules/{id}` | Detalhe |
| `PATCH /v1/automations/rules/{id}` | Edita campos e/ou liga/desliga (`{"enabled": false}`) |
| `DELETE /v1/automations/rules/{id}` | Exclui |
| `GET /v1/automations/runs` | Auditoria de execuções (status `executed`\|`skipped`\|`error`, `execution_log`) |

Execução: a cada evento do hub, as regras habilitadas com `event_type` correspondente são
avaliadas. Condições declarativas por chave do payload:

- `{"reason": "card_declined"}` — igualdade (strings sem caixa sensível);
- sufixos comparadores: `amount_gte`, `amount_lte`, `reason_ne` (`>=`, `<=`, `!=`);
- comparação contra o payload do evento achatado (+ `event_type`, `event_id`; o payload do
  ciclo de vida do pagamento usa `reason` como motivo da falha).

A auditoria fica em `automation_runs` (append-only, em espelho no hub). `UNIQUE(rule_id, event_id)`
+ short-circuit na avaliação garantem **no máximo uma execução por (regra, evento)** — at-least-once
sem efeitos duplicados. A regra seed `ar_seed_webhook_alert` escuta `webhook.failed` (entrega que
esgotou as tentativas = DLQ) e dispara `alerta_no_console` com a mensagem
“Webhook falhou e foi para a DLQ ({payment_id}, tentativa {attempts})”.

#### Dashboard e analytics

| Rota | Descrição |
| --- | --- |
| `GET /v1/analytics/summary` | KPIs: pagamentos nas janelas hoje/7d/30d, volume, taxa de falha, fila, DLQ |
| `GET /v1/analytics/timeseries` | Série por dia (`days=30`): `count`, `volume`, `failures` — alimenta os gráficos |
| `GET /v1/analytics/daily` | Rollups diários consolidados (`analytics_daily`, calculados pelo job agendado) |

#### Dados (exportações)

| Rota | Descrição |
| --- | --- |
| `GET /v1/data/exports/payments` | CSV streaming: id, status, método, valor, cliente, criado_em... |
| `GET /v1/data/exports/customers` | CSV streaming: id, email, nome, total pago, nº pagamentos, tags |

Headers de resposta incluem `Content-Disposition: attachment; filename="..csv"`; `kind` inválido →
`404 export_not_found`. A aba **Dados** do console combina as duas exportações com o rollup de
`GET /v1/analytics/daily` (tabela) e o feed de `GET /v1/events`.

#### Agente IA (chat + confirmação em 2 passos)

| Rota | Descrição |
| --- | --- |
| `POST /v1/agent/chat` | `{"question": "..."}`. Resposta: `{"object": "agent_reply", "answer", "sources", "action", "confirmation_required", "engine", "tools_used", "latency_ms"}` |
| `POST /v1/agent/confirm` | `{"token": "..."}`. Executa a ação pendente (2º passo) |

Fluxo em 2 passos para ações sensíveis (`retry_webhook` quando a pergunta menciona
retry/reprocess/reenvia com um `pay_...` ou `whd_...`; `criar_nota` para adicionar nota a um
cliente por e-mail ou `cus_...`):

1. `POST /v1/agent/chat` detecta a intenção e devolve
   `action.confirmation_required = true` + `action.token` + vencimento
   (`AGENT_CONFIRM_TTL_SECONDS`, 600 s). Nada é executado; o hub registra
   `agent.action.pending`.
2. O operador reenvia explicitamente `POST /v1/agent/confirm` com o token; aí sim a ação é
   executada e auditada (`agent_audit_log`, append-only). Token já usado → `409
   confirmation_already_used`.

Perguntas normais (resumo, consulta de pagamento, consulta de cliente, regras de automação) são
respondidas direto, com fontes RAG. A redação final usa um LLM opcional (`OPENAI_COMPATIBLE_*`);
sem LLM configurado, o fallback local responde com as mesmas fontes — sempre com `latency_ms
< 1500` no fallback.

Rate limit por IP+merchant (`AGENT_RATE_LIMIT_PER_MINUTE`, 30) para proteger o chat.

#### RAG

| Rota | Descrição |
| --- | --- |
| `GET /v1/rag/search?q=...&limit=5` | Busca léxica BM25 sobre payments, customers, event feeds, docs do projeto (`q` 2–300, `limit` 1–20) |
| `POST /v1/rag/reindex` | Força reindexação completa (payments, customers, event feeds do dia, docs); `?force=true` reprocessa o que já está íntegro. Resposta `202` com contadores |

Fontes indexadas e refresh: pagamentos e clientes atualizados na gravação; event feed do dia e
documentos do projeto (`README.md`, `specs/*.md` se `RAG_SPECS_DIR` existir) via job agendado
(`JOBS_RAG_SECONDS`). Sem dependência externa: indexador BM25 léxico próprio em SQLite.

---

## 7. Cartões de teste

O resultado é determinado pelos **últimos 4 dígitos**; o PAN completo é validado por Luhn e nunca
persistido.

| Número | Resultado | `failure_reason` |
| --- | --- | --- |
| `4242 4242 4242 4242` | Aprovado | — |
| `4000 0000 0000 0002` | Recusado | `card_declined` |
| `4000 0000 0000 9995` | Recusado | `insufficient_funds` |

- Bandeiras reconhecidas: Visa, Mastercard, Amex, Elo (demais → `other`).
- `exp_month` 1–12; `exp_year` 2000–2100 e não pode estar no passado.
- CVV: 3–4 dígitos.
- Use `exp_year` futuro nos exemplos (ex.: 2030).

Estados do cartão: `pending` (criado) → `processing` (worker) → `succeeded`/`failed`.

---

## 8. PIX

- O BR Code EMV é gerado **sincronamente** na criação, com chave, valor, merchant, cidade, txid e
  CRC16.
- `txid`: 26 caracteres alfanuméricos.
- Validade padrão: **30 minutos** (`PIX_EXPIRES_MINUTES`).
- O status permanece `pending` até `POST .../simulate-payment`.
- Ao expirar, o worker faz a transição `pending → failed` com `failure_reason = "pix_expired"` e
  emite `payment.failed`.
- O QR é retornado como texto (copia-e-cola) e como SVG em data URI.
- Nenhum PSP real é chamado.

---

## 9. Webhooks

### 9.1 Eventos

| Evento | Quando | Observações |
| --- | --- | --- |
| `payment.pending` | Criação de PIX | Inclui txid e `expires_at` |
| `payment.succeeded` | PIX confirmado ou cartão aprovado | Inclui `authorization_code` |
| `payment.failed` | Cartão recusado, saldo insuficiente ou PIX expirado | Inclui `failure_reason` |

`payment.processing` é registrado em `payment_events` para auditoria, mas **não** gera webhook.
Cartões não recebem `payment.pending` por webhook (apenas o evento de auditoria); o primeiro webhook
é o resultado final. Isso segue a lista de eventos da especificação.

### 9.2 Payload

```json
{
  "id": "evt_...",
  "type": "payment.succeeded",
  "created_at": "2026-10-26T12:00:05Z",
  "api_version": "2026-10-26",
  "data": {
    "payment": {
      "id": "pay_...",
      "object": "payment",
      "status": "succeeded",
      "method": "pix",
      "amount": 12990,
      "currency": "BRL",
      "description": "Pedido #123",
      "created_at": "2026-10-26T12:00:00Z",
      "updated_at": "2026-10-26T12:00:05Z",
      "authorization_code": null,
      "failure_reason": null,
      "pix": { "txid": "A1B2...", "expires_at": "2026-10-26T12:30:00Z" }
    }
  }
}
```

### 9.3 Headers

| Header | Descrição |
| --- | --- |
| `X-Event-Id` | ID do evento; use para deduplicar entregas |
| `X-Signature` | `t=<unix_ts>,v1=<hmac_sha256_hex>` |
| `X-Webhook-Attempt` | Número da tentativa (1-based) |
| `Idempotency-Key` | Mesmo valor de `X-Event-Id` |
| `Content-Type` | `application/json` |

Assinatura:

```text
v1 = HMAC-SHA256(secret, "<timestamp>." + raw_body)
```

O corpo assinado é o **raw body exato** (bytes), não uma reserialização do JSON.

### 9.4 Verificação (Python)

```python
import hashlib
import hmac
import time


def verify_webhook(secret: str, signature_header: str, raw_body: bytes,
                   tolerance_seconds: int = 300) -> bool:
    parts = {}
    for piece in signature_header.split(","):
        key, _, value = piece.strip().partition("=")
        if key and value:
            parts[key] = value

    try:
        timestamp = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        return False

    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        return False

    signed = f"{timestamp}.".encode("ascii") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
```

Rejeite timestamps fora de ±5 minutos (anti-replay) e deduplique por `X-Event-Id`.

### 9.5 Retry, backoff e DLQ

| Tentativa | Momento | Espera base |
| --- | --- | --- |
| 1 | imediata | — |
| 2 | após falha 1 | 10 s |
| 3 | após falha 2 | 30 s |
| 4 | após falha 3 | 2 min |
| 5 | após falha 4 | 10 min |
| 6 | após falha 5 | 1 h |
| — | após falha 6 | vai para DLQ (`status = failed`) |

- Atraso com **jitter** de ±20% (`WEBHOOK_JITTER_RATIO`).
- `2xx` = sucesso; qualquer outro status, timeout ou erro de rede = falha.
- `WEBHOOK_MAX_ATTEMPTS=6` significa **1 tentativa inicial + 5 retries**, alinhado aos cinco
  intervalos da especificação (`10, 30, 120, 600, 3600`).
- Cada tentativa é registrada em `webhook_attempts` (status HTTP, erro, duração).
- Entregas esgotadas ficam consultáveis em `GET /v1/webhook-deliveries?status=failed` e podem ser
  reenfileiradas via `POST .../retry`.
- Entregas são **at-least-once**; duplicatas são esperadas e devem ser ignoradas pelo merchant.

### 9.6 Demonstração de falha

```powershell
# As próximas 2 entregas responderão 503
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/demo/webhook-failures `
  -Headers @{ Authorization = "Bearer sk_demo_fluxpay_2026" } `
  -ContentType "application/json" `
  -Body '{"count":2}'
```

Depois crie/simule um PIX e acompanhe as tentativas no console ou em
`GET /v1/webhook-deliveries/{id}`. Para limpar:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/demo/reset-failures `
  -Headers @{ Authorization = "Bearer sk_demo_fluxpay_2026" }
```

---

## 10. Testes automatizados

```powershell
cd app_build
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Cobertura por arquivo:

| Arquivo | Foco |
| --- | --- |
| `tests/test_api.py` | Auth, validação, criação, consulta, listagem, segurança, health/metrics |
| `tests/test_idempotency.py` | Replay, conflito, concorrência real (uvicorn + threads), isolamento por merchant |
| `tests/test_state_machine.py` | Transições CAS válidas/inválidas, duplicatas, expiração, recuperação de fila |
| `tests/test_pix.py` | BR Code, CRC, txid, QR, simulação, expiração |
| `tests/test_webhooks.py` | Assinatura, headers, retry/backoff, DLQ, replay manual, deduplicação |
| `tests/test_crm.py` | 360°, notas, tags, segmentos declarativos, agregados e paginação |
| `tests/test_automations.py` | CRUD de regras, avaliação de eventos, ações (alerta/tag/retry), auditoria |
| `tests/test_events_ws.py` | Snapshot do hub, WS real (uvicorn), auth 4401, `after` 4400, heartbeat, fanout |
| `tests/test_agent_rag.py` | RAG (indexação, BM25, reindex), agente (fallback local, broadcast de eventos, confirm 2-passos), latência |
| `tests/test_data.py` | Exports CSV (payments/customers), rollup, feed de eventos |
| `tests/test_console_static.py` | Estáticos do console: scripts, CSP-compat, vendor Chart.js com header MIT |

**Última execução:** `80 passed` em ~49 s — os 38 testes do gateway (zero regressão) + 42 novos do
v0.3. A suíte v0.3 inclui um servidor uvicorn real para o teste de WS e outro para idempotência
concorrente. Além disso, foi executado um smoke test E2E do console v0.3 (abas, WS ao vivo, CRM,
automações, agente com confirmação, CSV e tema) descrito na seção 5.2.

Testes usam banco temporário e a API key `sk_test_fluxpay_automated_2026`; nenhum dado do demo é
tocado.

---

## 11. Configuração

Todas as variáveis vêm de ambiente ou de `.env` (ambiente real tem precedência). Defaults são de
demo.

| Variável | Default | Descrição |
| --- | --- | --- |
| `APP_ENV` | `demo` | `production` ativa travas de segurança |
| `DEMO_MODE` | `true` | Monta rotas demo e permite simulação |
| `DEMO_EXPOSE_API_KEY` | `true` | Expõe a key no bootstrap do console; proibido em produção |
| `HOST` | `0.0.0.0` | Interface de bind |
| `PORT` | `8000` | Porta HTTP |
| `DATABASE_PATH` | `./data/gateway.db` | Banco SQLite (diretório é criado) |
| `DB_BUSY_TIMEOUT_MS` | `15000` | Timeout de lock do SQLite |
| `MERCHANT_ID` | `mch_demo` | Merchant semeado |
| `MERCHANT_NAME` | `Loja Demo` | Nome exibido no PIX/console |
| `API_KEY` | `sk_demo_fluxpay_2026` | Bearer do merchant (armazenado como SHA-256) |
| `WEBHOOK_SECRET` | `whsec_demo_fluxpay_2026` | Segredo HMAC |
| `WEBHOOK_URL` | `http://127.0.0.1:8000/demo/webhook-receiver?endpoint=whe_demo` | Endpoint ativo |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | Timeout por entrega |
| `WEBHOOK_MAX_ATTEMPTS` | `6` | Total de tentativas (1 + 5 retries) |
| `WEBHOOK_RETRY_SCHEDULE` | `10,30,120,600,3600` | Espera base em segundos |
| `WEBHOOK_JITTER_RATIO` | `0.2` | Jitter ±20% |
| `WEBHOOK_SIGNATURE_TOLERANCE_SECONDS` | `300` | Janela anti-replay do receiver demo |
| `WEBHOOK_LEASE_SECONDS` | `60` | Lease de claim do dispatcher |
| `WEBHOOK_BATCH_SIZE` | `10` | Entregas por lote |
| `PIX_KEY` | `demo@fluxpay.local` | Chave PIX no BR Code |
| `PIX_CITY` | `SAO PAULO` | Cidade do merchant |
| `PIX_EXPIRES_MINUTES` | `30` | Validade do QR (1–1440) |
| `CARD_AUTHORIZATION_DELAY_MS` | `350` | Latência simulada fora da transação |
| `WORKER_ENABLED` | `true` | Liga worker + dispatcher |
| `WORKER_POLL_SECONDS` | `0.25` | Intervalo do worker |
| `DISPATCHER_POLL_SECONDS` | `0.5` | Intervalo do dispatcher |
| `QUEUE_BATCH_SIZE` | `10` | Mensagens por claim |
| `QUEUE_MAX_ATTEMPTS` | `5` | Tentativas máximas da fila antes de `failed` |
| `QUEUE_LEASE_SECONDS` | `120` | Lease de mensagens em processamento |
| `MAX_BODY_BYTES` | `262144` | Limite de corpo (256 KiB) |
| `LOG_LEVEL` | `INFO` | Nível de log |
| `EVENT_FANOUT` | `true` | Liga o hub de eventos: fanout para automações/RAG + broadcast WS |
| `WS_ENABLED` | `true` | Monta `WS /ws/events` |
| `WS_POLL_SECONDS` | `0.15` | Intervalo de polling do broadcaster |
| `WS_HEARTBEAT_SECONDS` | `20` | Intervalo do `server.ping` |
| `EVENT_STREAM_RETENTION_DAYS` | `30` | Retenção do hub antes da passada de limpeza |
| `JOBS_ENABLED` | *(segue `WORKER_ENABLED`)* | Liga os jobs agendados independentemente do worker |
| `JOBS_SEGMENTS_SECONDS` | `60` | Recalcula segmentos declarativos |
| `JOBS_ANALYTICS_SECONDS` | `180` | Consolida rollups diários |
| `JOBS_RAG_SECONDS` | `300` | Reindexa event feeds do dia + docs |
| `JOBS_RETENTION_SECONDS` | `900` | Varre expirações de confirmação e retenção do hub |
| `ANALYTICS_ROLLUP_DAYS` | `92` | Janela de rollup consolidado |
| `RAG_ENABLED` | `true` | Liga indexação e busca RAG |
| `RAG_SPECS_DIR` | `../specs` | Diretório de specs `.md` indexadas (inexistente → degrada sem erro) |
| `AGENT_RATE_LIMIT_PER_MINUTE` | `30` | Rate limit do chat por merchant |
| `AGENT_LLM_TIMEOUT_SECONDS` | `30` | Timeout da chamada ao LLM opcional |
| `AGENT_CONFIRM_TTL_SECONDS` | `600` | Validade do token de confirmação |
| `PII_REDACT_EMAIL` | `0` | `1` mascara e-mails no texto das respostas do agente |
| `OPENAI_COMPATIBLE_BASE_URL` | *(vazio)* | LLM opcional compatível com OpenAI (segredos fora do código) |
| `OPENAI_COMPATIBLE_API_KEY` | *(vazio)* | API key do LLM opcional |
| `OPENAI_COMPATIBLE_MODEL` | *(vazio)* | Modelo do LLM opcional |

O merchant e o endpoint demo são semeados de forma idempotente no boot. Para semear/inspecionar
manualmente (a key é armazenada hasheada):

```powershell
python -m app.seed_cli            # mostra apenas hints mascarados
python -m app.seed_cli --print-key
```

### Produção

Com `APP_ENV=production`:

- `DEMO_MODE` e `DEMO_EXPOSE_API_KEY` devem ser `false`;
- `API_KEY` e `WEBHOOK_SECRET` devem ser definidos no ambiente ou no `.env` (sem defaults);
- as rotas `/demo/*` não são montadas;
- a aplicação recusa iniciar se alguma trava for violada.

Ainda assim, TLS, proxy reverso, backup e rotação de segredos são responsabilidade do deploy. O
SQLite é adequado ao escopo local/single-node deste projeto.

---

## 12. Segurança e privacidade

- API keys armazenadas apenas como SHA-256; comparação feita no banco.
- Bearer obrigatório em `/v1/*`; respostas não revelam se um pagamento pertence a outro merchant.
- PAN e CVV nunca são gravados em banco, logs ou eventos. O cartão participa apenas da fingerprint
  de idempotência e do resultado simulado (last4).
- Erros de validação removem `input` e `ctx` para não ecoar PAN/CVV.
- Webhooks assinados com HMAC-SHA256 sobre timestamp + raw body; receiver valida janela de ±5 min.
- O receiver demo deduplica entregas por `X-Event-Id`; merchants reais devem fazer o mesmo.
- Headers de segurança em todas as respostas (`nosniff`, `DENY`, CSP, `no-referrer`,
  `Permissions-Policy`) e `Cache-Control: no-store` nas rotas sensíveis.
- Request ID (`X-Request-Id`) aceito apenas com charset seguro e propagado nos logs estruturados.
- SQL sempre parametrizado; sem concatenação de entrada do usuário.
- Limite de corpo (256 KiB) e timeouts de webhook.
- Imagem Docker non-root, sem segredos embutidos.

---

## 13. Estrutura de arquivos

```text
app_build/
├─ app/
│  ├─ __init__.py                 # versão
│  ├─ __main__.py                 # entrypoint: python -m app
│  ├─ config.py                   # Settings, .env loader, travas de produção
│  ├─ db.py                       # schema SQLite, WAL, transações, triggers append-only
│  ├─ errors.py                   # ApiError + envelope
│  ├─ logging_setup.py            # logs JSON + request-id
│  ├─ main.py                     # app factory, middleware, handlers, rotas/static
│  ├─ pix.py                      # BR Code EMV, CRC16, txid, QR SVG
│  ├─ schemas.py                  # modelos Pydantic e validações
│  ├─ security.py                 # HMAC, hash de API key, Luhn, bandeira, cartões de teste
│  ├─ seed_cli.py                 # CLI de seed opcional
│  ├─ utils.py                    # IDs, tempo ISO, JSON canônico
│  ├─ api/
│  │  ├─ deps.py                  # auth Bearer e injeções
│  │  ├─ agent.py                 # /v1/agent/chat + /v1/agent/confirm (2 passos)
│  │  ├─ analytics.py             # /v1/analytics/summary|timeseries|daily
│  │  ├─ automations.py           # /v1/automations/rules|runs
│  │  ├─ crm.py                   # /v1/customers* e /v1/segments
│  │  ├─ data.py                  # /v1/data/exports/{payments,customers}
│  │  ├─ demo.py                  # console bootstrap, receiver, fault injection
│  │  ├─ events_api.py            # /v1/events + WS /ws/events
│  │  ├─ payments.py              # /v1/payments*
│  │  ├─ rag.py                   # /v1/rag/search|reindex
│  │  ├─ system.py                # /health, /v1/me, /v1/metrics
│  │  └─ webhooks.py              # endpoints, deliveries, DLQ retry
│  ├─ services/
│  │  ├─ agent.py                 # agente IA: intents, tools, RAG, confirm
│  │  ├─ analytics.py             # KPIs, timeseries, rollups, exports CSV
│  │  ├─ automations.py           # regras, avaliação de eventos, ações, auditoria
│  │  ├─ crm.py                   # customers 360°, notes, tags, segments
│  │  ├─ dispatcher.py            # loop do dispatcher
│  │  ├─ events.py                # event hub: append, snapshot, retenção
│  │  ├─ jobs.py                  # jobs agendados (segmentos, rollup, RAG, retenção)
│  │  ├─ metrics.py               # snapshot operacional
│  │  ├─ payments.py              # idempotência, criação, CAS, expiração
│  │  ├─ queue.py                 # fila durável com lease e retry
│  │  ├─ rag.py                   # corpus, indexação, busca BM25
│  │  ├─ seed.py                  # merchant + endpoint demo + regras seed
│  │  ├─ webhook_admin.py         # consulta/replay de entregas
│  │  ├─ webhooks.py              # eventos, outbox, HMAC, retry, DLQ
│  │  ├─ worker.py                # loop do worker
│  │  └─ ws.py                    # registro de conexões, broadcast, heartbeat
│  └─ static/
│     ├─ index.html               # console demo (6 abas)
│     ├─ styles.css               # tema escuro/claro + componentes v0.3
│     ├─ app.js                   # core + navegação por abas
│     ├─ theme.js                 # tema claro/escuro persistido
│     ├─ ws.js                    # cliente WS com reconnect + reconciliação
│     ├─ dashboard.js             # gráficos Chart.js dos KPIs
│     ├─ crm.js                   # CRM (tabela, 360°, notas, tags, segmentos)
│     ├─ automations.js           # regras de automação
│     ├─ agent.js                 # chat + confirmação em 2 passos
│     ├─ data.js                  # rollup, CSV, feed de eventos
│     ├─ vendor/chart.umd.js      # Chart.js 4.5.0 vendored (MIT, sem CDN)
│     └─ favicon.svg
├─ tests/
│  ├─ conftest.py                 # fixtures + servidor HTTP real
│  ├─ helpers.py
│  ├─ test_agent_rag.py
│  ├─ test_automations.py
│  ├─ test_console_static.py
│  ├─ test_crm.py
│  ├─ test_data.py
│  ├─ test_events_ws.py
│  ├─ test_api.py
│  ├─ test_idempotency.py
│  ├─ test_pix.py
│  ├─ test_state_machine.py
│  └─ test_webhooks.py
├─ .dockerignore
├─ .env.example
├─ .gitignore
├─ Dockerfile                     # multi-stage, UID 10001, healthcheck
├─ docker-compose.yml             # volume nomeado, restart, healthcheck
├─ pyproject.toml                 # metadados + configuração pytest
├─ requirements.txt
├─ requirements-dev.txt
├─ run.ps1                        # bootstrap Windows em um comando
├─ README.md
└─ data/                          # criado em runtime (ignorado pelo Git/Docker)
```

Arquivos gerados não versionados: `.venv/`, `.env`, `data/`, `__pycache__/`, `.pytest_cache/`.

---

## 14. Decisões, extensões e desvios da especificação

Itens implementados conforme a especificação:

- Contratos de `POST /v1/payments`, `GET /v1/payments/{id}` e `POST .../simulate-payment`.
- `202` na criação, `200` + `Idempotent-Replay: true` no replay, `409` no conflito de payload.
- Máquina de estados `pending → processing → succeeded|failed` com CAS e `pending → failed` na
  expiração.
- Fila durável em banco, at-least-once, worker idempotente e recuperação após restart.
- Outbox transacional, HMAC-SHA256, retry com jitter, DLQ e log de tentativas.
- PAN/CVV nunca persistidos; Luhn; bandeira + last4; cartões de teste determinísticos.
- PIX com txid, copia-e-cola EMV e validade de 30 min.
- Console demo, Docker multi-stage non-root, healthcheck, `.dockerignore`, Compose com volume.
- Testes automatizados de concorrência, estados, assinatura e retry.

Decisões e extensões que vão além do mínimo (sem alterar contratos obrigatórios):

1. **`payment.pending` só é enviado por webhook para PIX.** Cartões emitem o evento de auditoria
   `payment.pending`, mas o primeiro webhook é o resultado final. Isso segue a lista explícita de
   eventos da especificação (`payment.pending` (PIX), `succeeded`, `failed`). `payment.processing`
   é auditado, não notificado.
2. **Retry = 1 tentativa inicial + 5 retries** (`WEBHOOK_MAX_ATTEMPTS=6`). A especificação lista
   cinco intervalos (`10s, 30s, 2min, 10min, 1h`) e fala em “5 tentativas”; a implementação usa os
   cinco intervalos como retries após a primeira tentativa. Total de 6 POSTs antes da DLQ.
3. **Imagem do QR em SVG** (data URI) via `segno`, além do payload textual EMV. A fonte de verdade
   continua sendo o copia-e-cola.
4. **Rotas extras de operação/demo:** `/v1/me`, `/v1/metrics`, `/v1/payments/{id}/events`,
   gerenciamento de webhook endpoints/deliveries, DLQ replay, receiver demo e injeção de falhas.
5. **`simulate-payment` retorna `409`** para pagamento já terminal, expirado ou de cartão, em vez de
   fingir sucesso. É uma proteção de consistência.
6. **Idempotência inclui o cartão na fingerprint**, mas não persiste PAN/CVV. Replays exigem o mesmo
   payload; o corpo armazenado nunca contém dados do cartão.
7. **Sem expiração de chaves de idempotência** na v1; retenção indefinida é adequada ao escopo demo.
8. **Docker não executado neste ambiente** por ausência do Docker CLI/engine. Os arquivos foram
   validados estaticamente (contexto, permissões, healthcheck, volume, `.dockerignore`) e a execução
   nativa foi validada de ponta a ponta. O build deve ser confirmado em uma máquina com Docker
   Desktop.

### 14.1 Decisões da extensão v0.3 (CRM/Dashboard)

1. **`specs/` fora do contexto Docker.** O contexto de build é `app_build/`; copiar `../specs` para
   dentro da imagem exigiria mudar o contexto e engolir arquivos de documentação no runtime. O
   fallback escolhido: a imagem indexa o `README.md` embutido + feeds de eventos, e quem quiser as
   specs no RAG bind-monta o diretório e define `RAG_SPECS_DIR` (Compose traz o exemplo comentado).
   O serviço degrada sem erro quando o diretório não existe.
2. **Nunca apagar regras semeadas.** O seed cria o merchant, o endpoint demo e a regra
   `ar_seed_webhook_alert` de forma idempotente a cada boot.
3. **Efeitos idempotentes nas automações.** `UNIQUE(rule_id, event_id)` + short-circuit na
   avaliação garante no máximo uma execução por (regra, evento), mesmo com reavaliações.
4. **Ferramenta com confirmação (`confirm`) separada da resposta.** O 1º passo nunca executa
   efeito; o token TTL curto evita confirmações autoritativas penduradas para sempre.
5. **Fallback local do agente sem LLM** (A-09): o chat nunca quebra sem `OPENAI_COMPATIBLE_*`;
   `engine` informa `local` vs `llm` na resposta.
6. **Chart.js vendored**, não CDN, para respeitar o CSP `self` e funciona offline.
7. **Tema persistido** em `localStorage` + `prefers-color-scheme` no primeiro acesso.

### 14.2 Limitações v0.3

- **Automações:** até 10 condições por regra (`==`, `_ne`, `_gte`, `_lte`); sem `OR`, strings com curinga ou campos aninhados.
- **Segmentos:** definição declarativa (`recency_days`, `frequency_min`, etc.) sem filtros custom.
- **Dashboard:** sem histórico de KPIs de fila/DLQ — só o estado atual por janela.
- **WS:** single-node; sem persistência de conexões entre processos ou multicast multi-worker.
- **RAG:** BM25 léxico próprio; sem embedding vetorial, reranking ou OCR de documentos.
- **Agente:** rate limit por processo (`AGENT_RATE_LIMIT_PER_MINUTE`), sem limitador global entre réplicas.

---

## 15. Limitações conhecidas

- Single-node: SQLite, worker e dispatcher no mesmo processo por padrão.
- Provedores 100% simulados; nenhuma cobrança real, estorno, cancelamento ou chargeback.
- Sem 3-D Secure, antifraude, multimoeda, recorrência ou split.
- Sem certificação PCI formal; use somente dados fictícios.
- Sem TLS embutido; em produção, coloque atrás de um proxy reverso.
- O console é uma ferramenta de demonstração, não um painel administrativo completo.
