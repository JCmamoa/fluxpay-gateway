# Especificação Técnica — CRM, Dashboard em Tempo Real, Automação, Agent IA, RAG e Dados

## FluxPay v0.3 (extensão do Gateway de Pagamento v0.2)

| Campo | Valor |
| --- | --- |
| Versão | 0.2 (Aprovada) |
| Status | ✅ Aprovada pelo usuário — LLM plugável, 3 ações, tema claro/escuro |
| Autor | @pm |
| Base | `specs/payment-gateway.md` v0.2 (aprovada) |
| Data | 27/10/2026 |

> **Documento technology-agnostic.** Continua a numeração de requisitos do produto (RF-12+). Tudo que já existe permanece: PIX/cartões, fila durável, idempotência, webhooks, Docker.

---

## 1. Contexto e problema

O gateway v0.2 processa pagamentos, mas o merchant não tem visão operacional nem ferramentas de gestão. O v0.3 adiciona:

1. **CRM** — visão 360° dos clientes (histórico, notas, tags, segmentos);
2. **Dashboard em tempo real** — gráficos e métricas que se atualizam ao vivo (WebSocket);
3. **Automação** — regras evento→ação executadas pela fila (com auditoria);
4. **Agent IA** — assistente que consulta os dados e responde no próprio dashboard;
5. **RAG** — recuperação aumentada: indexa pagamentos, clientes, eventos e docs para respostas fundamentadas;
6. **Dados** — camada analítica: rollups diários, exportação CSV, métricas derivadas.

## 2. Objetivos

- **OBJ-05** — Evento → gráfico do dashboard atualizado: **≤ 1 s (p95)** via WebSocket.
- **OBJ-06** — Resposta do assistente: ≤ 5 s (p95) com LLM; ≤ 1,5 s no fallback local sem LLM.
- **OBJ-07** — **Nenhuma chave de API externa obrigatória** para rodar o projeto (LLM é opcional e plugável).
- **OBJ-08** — Zero regressões: os 38 testes atuais continuam verdes e a suíte cresce para cobrir o v0.3.

## 3. Escopo

### 3.1 Em escopo (v1)

- **CRM**: aba de clientes no console com visão 360° (pagamentos, eventos, notas, tags), criação automática do cliente a partir do pagamento (`customer.email` como chave natural), segmentos calculados por regra (recência/frequência/valor/método).
- **Dashboard**: página única com gráficos (volume por período, taxa de sucesso, mix PIX/cartão, top motivos de falha, fila e DLQ, top clientes) e números grandes, atualização **em tempo real** via WebSocket + snapshot inicial por REST.
- **Hub de eventos interno**: todos os eventos `payment.*` e `webhook.*` gravados em `event_stream` (append-only) e publicados em tempo real; reconciliação por snapshot REST.
- **Automação**: CRUD de regras (evento + condições + ação), execução pelo worker existente, auditoria completa (`automation_runs`), toggle on/off. Ações v1: `alerta_no_console`, `tag_customer`, `retry_webhook`.
- **Agent IA**: chat no console com ferramentas read-only por padrão + 2 ações sensíveis (`criar_nota`, `retry_webhook`) que exigem **confirmação explícita do operador**; respostas fundamentadas no RAG; audit log de cada interação.
- **RAG**: indexer incremental (pagamentos, clientes, eventos, automações e docs do projeto), índice local (BM25/TF-IDF em SQLite), citação das fontes na resposta, reindex em runtime.
- **Dados**: tabelas analíticas (rollups diários por método/status/receita), endpoints analíticos, export CSV (pagamentos, clientes), retenção configurável.

### 3.2 Fora de escopo (v1)

- Treinamento de modelos, fine-tuning, embeddings via serviço externo.
- LLM hospedado como fluxo obrigatório (é plano B opcional via env).
- Marketing/push de funil completo, A/B tests, marketing por e-mail/WhatsApp.
- Multi-tenancy, SSO, permissões por usuário do console.
- BI avançado (SQL customizado, dashboards salvos, alertas agendados por e-mail).

## 4. Personas

- **Operador (merchant)** — acompanha o negócio no dashboard ao vivo, usa CRM e assistente.
- **Integrador** — continua consumindo a API de pagamentos (sem mudanças breaking).
- **Analista** — usa Dados e Automações para métricas e rotinas.

## 5. Jornadas e histórias de usuário

- **US-08** — Como operador, vejo o número "receita de hoje" e o gráfico de pagamentos se atualizarem no instante em que uma aprovação acontece (sem refresh).
- **US-09** — Como operador, clico em um cliente e vejo a visão 360°: pagamentos, eventos, tags e notas.
- **US-10** — Como operador, crio a regra "webhook falhar 2x → alerta no console" e vejo o alerta chegar ao vivo.
- **US-11** — Como operador, pergunto ao assistente "quantos PIX foram aprovados hoje?" e recebo a resposta correta com a fonte citada.
- **US-12** — Como analista, exporto pagamentos e rollups em CSV para conferência externa.
- **US-13** — Como operador, ativo a regra "3 aprovações → tag `frequent`" e vejo o cliente segmentado.

## 6. Requisitos funcionais

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-12 | CRM: clientes auto-criados a partir do pagamento (`customer.email`), visão 360°, notas, tags e segmentos calculados | P0 |
| RF-13 | Dashboard com gráficos + métricas numéricas atualizando **ao vivo** via WebSocket (snapshot inicial via REST) | P0 |
| RF-14 | Hub de eventos internos: gravação auditável em `event_stream` e broadcast em tempo real | P0 |
| RF-15 | Motor de automação: CRUD de regras, execução na fila existente, ações v1 (`alerta_no_console`, `tag_customer`, `retry_webhook`), auditoria completa | P0 |
| RF-16 | Agent IA no console: chat com ferramentas read-only por padrão + ações sensíveis exigindo confirmação; audit log | P0 |
| RF-17 | RAG local: indexer incremental (pagamentos, clientes, eventos, docs), BM25/TF-IDF local, citação das fontes | P0 |
| RF-18 | Camada de dados: rollups diários, endpoints analíticos, export CSV, retenção configurável | P1 |
| RF-19 | LLM **plugável** via env (OpenAI-compatible); sem chave → **fallback determinístico** local (template/extrativo) — sem bloquear o projeto | P1 |
| RF-20 | Console com **tema claro e escuro**: toggle no header, escolha persistida localmente e default seguindo `prefers-color-scheme` | P0 |

## 7. Regras de negócio

- Cliente criado automaticamente no primeiro pagamento com `customer.email`; e-mail é a chave natural (case-insensitive; dedupe no matching).
- Segmentação por definição declarativa (ex.: recência ≤ 30d, frequência ≥ 3), recalculada por job agendado e em eventos relevantes.
- `event_stream` é o ponto central: toda mudança relevante de pagamento/webhook/crm/automação entra nele com namespace `payment.*`, `webhook.*`, `crm.*`, `automation.*`, `agent.*`.
- Agente: read-only por padrão; lê dados do merchant local, mas **nunca** executa mutação sem confirmação explícita do operador no chat; PII mínima nos prompts; segredos/keys nunca entram no contexto do modelo; audit log por interação (pergunta, fontes citadas, ação tomada).
- Automação: regras `enabled/disabled`; cada disparo registra `automation_runs` (evento, condições avaliadas, ação, resultado); `retry_webhook` reutiliza o retry/DLQ existente e é idempotente.
- Fallback determinístico: sem LLM configurado, respostas saem de template + extração direta do RAG; sem evidência suficiente → responde "sem dados suficientes" indicando as fontes consultadas (nunca inventa números).
- Rollups diários: gerados por job + recomputados idempotentemente (`analytics_daily` com upsert).

## 8. Arquitetura lógica

```text
            ┌────────────────────────── Console (HTML/JS) ─────────────────────────┐
            │   Dashboard (WS live) │ CRM │ Automações │ Assistente │ Dados      │
            └───┬──────────────┬──────────────┬────────────────┬─────────────┬─────┘
                │ REST         │ REST         │ REST           │ REST        │ WS
                ▼              ▼              ▼                ▼             │
  API HTTP (FastAPI) ── EventHub (grava `event_stream`) ── WS Broadcaster ────┘
                │
                ▼
  Worker ─→ [pagamentos (existente)] [webhooks (existente)] [automações] [RAG indexer] [rollups]
                │
                ▼
  RAG pipeline: fontes → rag_documents/chunks (SQLite, BM25/TF-IDF) → retriever → generator
                │
                ▼
  Agent: tools (read: pagamentos/clientes/eventos/métricas; write: nota/retry_webhook com confirmação)
         LLM plugável via env (OpenAI-compatible) → se ausente: fallback local determinístico
```

**Restrições de arquitetura (obrigatórias):**

- **ARQ-08** — Zero dependência externa obrigatória: charts e assets **vendored** no projeto (sem CDN), índice RAG local, LLM opcional com fallback determinístico.
- **ARQ-09** — Reusar a fila durável e o padrão idempotente do v0.2 (automações/RAG/rollups são consumidores da fila, entrega at-least-once).
- **ARQ-10** — WS é notificação, REST é source of truth: reconexão automática + `?after=<last_event_id>` + snapshot REST para reconciliar estado.
- **ARQ-11** — Guardrails do agente: PII mínima no prompt, zero segredos no contexto, mutação sempre com confirmação, audit log completo.
- **ARQ-12** — Charts sem CDN: `chart.js` vendored em `app/static/vendor/` (MIT) ou gráficos SVG próprios — decisão de implementação do @engineer.

## 9. Modelo de dados (adições)

```text
customers(id, email UNIQUE_CI, name, phone, lifecycle_status,
          first_seen_at, last_seen_at, created_at, updated_at)
customer_events(id, customer_id, type, payload, created_at)      -- timeline 360°
customer_notes(id, customer_id, author, body, pinned, created_at)
customer_tags(customer_id, tag, created_at)
segments(id, name, definition, computed_at)                     -- definition json
segment_members(segment_id, customer_id, added_at)

automation_rules(id, name, event_type, conditions, action_type,
                 action_config, enabled, created_at, updated_at)
automation_runs(id, rule_id, event_type, event_id, status, detail, created_at)

rag_documents(id, source_type, source_ref, title, content, content_hash, indexed_at)
rag_chunks(id, document_id, ordinal, text, terms, created_at)    -- índice local

analytics_daily(day, method, attempts, succeeded, failed, volume_cents,
                approval_rate, UNIQUE(day, method))

event_stream(id, type, payload, created_at)                     -- hub central (append-only)
agent_logs(id, question, rag_sources, action_taken, answer, latency_ms, created_at)
```

**Constraints:**

- `event_stream` **append-only** (mesma filosofia do `payment_events`).
- `rag_chunks.terms` = índice invertido compacto para BM25/TF-IDF local.
- `analytics_daily` em upsert idempotente.
- Índices: `customer_events(customer_id, created_at)`, `event_stream(type, created_at)`, `automation_runs(rule_id, created_at)`.

## 10. Contratos de API

**Autenticação REST:** mesmos `Bearer <merchant_api_key>` do v0.2. **WS:** token na query (`?token=...`) validado no handshake.

### REST (novos)

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/v1/customers` | Lista paginada (filtros: tag/segmento/método/valor) |
| GET | `/v1/customers/{id}` | Visão 360° (pagamentos, eventos, tags, notas, segmentos) |
| POST | `/v1/customers/{id}/notes` | Adiciona nota (auditada em `customer_events`) |
| POST | `/v1/customers/{id}/tags` | Adiciona tag |
| GET/POST | `/v1/segments` | Lista/cria segmentos declarativos |
| GET | `/v1/analytics/summary` | Snapshot de métricas (hoje/7d/30d, taxa, mix, fila, DLQ) |
| GET | `/v1/analytics/timeseries?metric=&granularity=&from=&to=` | Séries para os gráficos |
| CRUD | `/v1/automations/rules` | Lista/cria/edita/ativa/desativa regras |
| GET | `/v1/automations/runs` | Auditoria de execuções |
| POST | `/v1/agent/chat` | `{ "question": "..." }` → resposta com `sources[]` e, quando aplicável, ação com `confirmation_required` |
| POST | `/v1/agent/confirm` | Confirma ação sensível pendente |
| POST | `/v1/rag/reindex` | Força reindexação (dados + docs) |
| GET | `/v1/data/exports/{payments\|customers}` | CSV streaming |
| GET | `/v1/analytics/daily?from=&to=` | Rollups diários |

### WebSocket

- **`WS /ws/events`** — broadcast de `event_stream` ao vivo: payload `{id, type, created_at, data}`; reconexão com `?after=<last_event_id>`; heartbeat do servidor; snapshot REST cobre estados perdidos.

### Console (UI)

Abas novas: **Dashboard** (live), **CRM**, **Automações**, **Assistente**, **Dados** — mesma linguagem visual do console atual. Gráficos: linhas (volume/aprovação), donut (mix métodos), barras (top falhas), tabela top clientes; números grandes: receita hoje, aprovações, fila, DLQ, webhooks falhos.

## 11. Requisitos não funcionais

- **RNF-10** — Tempo real: ocorrência → broadcast WS ≤ 500 ms (nível aplicativo); gráfico atualizado ≤ 1 s (p95).
- **RNF-11** — Agente: p95 ≤ 5 s com LLM; ≤ 1,5 s no fallback local; timeout por chamada; latência registrada no audit log.
- **RNF-12** — Zero chaves obrigatórias: roda sem env de LLM (fallback) e sem CDN nos assets.
- **RNF-13** — PII/segurança: PII mínima no prompt; segredos nunca no contexto; dados expostos apenas do merchant consultado; WS exige token válido; rate limit no chat.
- **RNF-14** — Audit: `automation_runs` e `agent_logs` append-only.
- **RNF-15** — Zero regressões: os 38 testes v0.2 verdes; suíte ≥ 60 testes cobrindo CRM, WS, automações, RAG e dados.
- **RNF-16** — Performance: dashboard renderiza com 90 dias de dados; RAG indexa 10k pagamentos em ≤ 60 s; retention de `event_stream` (default 30d, configurável).
- **RNF-17** — Docker: nenhum passo manual na imagem; `Dockerfile`/`compose` estendidos sem quebrar `docker compose up --build` e o healthcheck.

## 12. Critérios de aceitação

- **CA-11** — Aprovação de pagamento → métricas e gráficos do dashboard atualizam via WS em ≤ 1 s (p95), sem refresh.
- **CA-12** — Reconexão WS: cliente volta e reconcilia por `after` + snapshot REST idêntico ao estado real.
- **CA-13** — Cliente criado no primeiro pagamento com `customer.email`; unificação por e-mail (case-insensitive) sem duplicatas.
- **CA-14** — Regra "webhook falhou 2x → alerta" dispara, registra `automation_runs` e o alerta aparece no console ao vivo.
- **CA-15** — "Quantos PIX aprovados hoje?" → valor correto do banco + fonte citada; validado contra dados reais no teste.
- **CA-16** — Fallback sem LLM: mesmas perguntas respondidas por template/extração em ≤ 1,5 s; sem evidência → "sem dados suficientes" (nunca inventa).
- **CA-17** — Ação sensível (`criar_nota`/`retry_webhook`) só executa após confirmação explícita; sem confirmação nada acontece (testado).
- **CA-18** — Export CSV: header correto, valores em centavos íntegros, ordem determinística.
- **CA-19** — Rollup diário idempotente em reexecução (upsert) e consistente com `payments`.
- **CA-20** — Zero regressão: os 38 testes v0.2 passam sem mudanças; suíte expandida verde.
- **CA-21** — `docker compose up --build` inclui dashboard/WS/CRM/RAG sem passos manuais.
- **CA-22** — Toggle claro/escuro aplica-se a todas as abas (incluindo gráficos), persiste entre reloads e respeita `prefers-color-scheme` como default.

## 13. Premissas e decisões

- **A-09** — LLM é opcional; sem env configurado, o agente opera no fallback determinístico.
- **A-10** — WS substitui polling no dashboard; REST permanece como fonte autoritativa.
- **A-11** — Índice RAG é BM25/TF-IDF local em SQLite (sem embeddings externos); busca lexical + filtros estruturais é suficiente para perguntas operacionais.
- **A-12** — Charts vendored (`chart.js` MIT em `app/static/vendor/`) ou SVG próprio — decisão do @engineer.
- **A-13** — PII mínima nos prompts: sem PAN/CVV (já inexistente); mascaramento de e-mail controlado por env `PII_REDACT_EMAIL=1`.
- **A-14** — RAG reindexa incrementalmente (`content_hash` evita retrabalho) + full refresh sob demanda.
- **A-15** — `retry_webhook` do agente reutiliza o retry/DLQ existentes (idempotente).
- **A-16** — Tema claro/escuro com CSS variables + toggle persistido em `localStorage`; cores dos gráficos acompanham o tema (contraste garantido nos dois temas).

## 14. Decisões aprovadas

1. **LLM**: plugável via env (OpenAI-compatible), com fallback determinístico local — sem chave obrigatória.
2. **Automações v1**: as 3 ações (`alerta_no_console`, `tag_customer`, `retry_webhook`) cobrem o escopo.
3. **CRM v1**: perfil 360° + notas/tags/segmentos (sem funil/vendedor).
4. **Charts**: `chart.js` vendored, sem CDN.
5. **Tema**: claro e escuro com toggle no console (RF-20, CA-22).

---

## ✅ Aprovada

Especificação aprovada pelo usuário em 27/10/2026 (v0.2). O @pm aciona: **@engineer → @sentinel → @devops**.
