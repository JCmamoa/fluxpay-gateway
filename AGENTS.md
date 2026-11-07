# AGENTS.md — Projeto Padrão (FluxPay)

Repositório gerido por um time autônomo de agents OpenCode (`opencode.jsonc` → `default_agent: pm`).
Idioma do projeto: **pt-BR** (docs, UI, especificações e mensagens de commit).

## Estrutura — a raiz NÃO é a aplicação

- `app_build/` — **toda** a aplicação executável (FastAPI + SQLite). Rode todos os comandos a partir daqui.
- `specs/` — especificações técnicas aprovadas (fonte de verdade). Nova feature → spec nova aqui, implementação em `app_build/`.
- `.opencode/agents/` — fluxo do time: `@pm` (spec + aprovação do usuário) → `@engineer` (implementa em `app_build/`) → `@qa` → `@devops` (sobe localmente). O pm só orquestra após aprovação explícita do usuário.
- `app_build/landing/<segmento>/` — 4 landing pages estáticas autocontidas (HTML com CSS/JS embutido, sem build) + PWA em `app/` (manifest, sw.js). Separadas do console FastAPI.
- `data/`, `.venv/`, `.env` — runtime/gitignored, nunca versionar nem editar.

## Comandos (sempre em `app_build/`, ambiente Windows/PowerShell)

```powershell
.\run.ps1                  # sobe tudo: cria .venv, instala deps, copia .env.example→.env, roda python -m app
.\run.ps1 -Port 8080       # ajusta PORT e WEBHOOK_URL juntos
.\.venv\Scripts\python.exe -m pytest -q          # suíte completa (~50 s)
.\.venv\Scripts\python.exe -m pytest tests\test_webhooks.py -q   # um arquivo; adicione -k "nome" para filtrar
docker compose up --build  # alternativa; contexto de build é app_build/
```

- `run.ps1` é Windows-only. Fora do Windows: venv manual + `pip install -r requirements.txt` + `python -m app`.
- CI = apenas `python -m pytest -q` (Python 3.12, a partir de `app_build/`). **Não há lint nem typecheck configurados** — pytest é o único gate; não invente comandos de lint.
- Testes exigem `requirements-dev.txt`. Alguns testes sobem servidores uvicorn reais (WS, idempotência concorrente) — lentidão é esperada.
- Testes usam banco temporário e a key `sk_test_fluxpay_automated_2026`; nunca tocar nos dados do demo (`data/gateway.db`).

## Regras de domínio (quebrar = bug)

- Valores monetários são **inteiros em centavos**, moeda só `BRL`.
- Idempotência: `Idempotency-Key` + fingerprint; replay → `200` + `Idempotent-Replay: true`; payload diferente → `409`.
- Máquina de estados com CAS: `pending → processing → succeeded|failed`.
- Webhook é outbox transacional (gravado na mesma transação do pagamento); HMAC-SHA256 sobre `t=<ts>.` + raw body.
- Tabelas append-only (`payment_events`, `webhook_attempts`, `agent_logs`) são protegidas por triggers no banco — não criar UPDATE/DELETE nelas.
- PAN/CVV jamais persistidos ou logados; API key só como SHA-256.
- Seed de boot é idempotente e recria merchant + regra `ar_seed_webhook_alert` — não apagar seeds.
- Console (em `app/static/`): JS vanilla + Chart.js **vendored** (CSP `self`, sem CDN).
- RAG indexa `../specs` via `RAG_SPECS_DIR` (relativo a `app_build/`); degrada em silêncio se o diretório não existir.
- Docker: porta interna sempre 8000; `WEBHOOK_URL` aponta para loopback interno mesmo com `PORT` diferente no host.

## Convenções

- Commits em pt-BR, estilo conventional: `feat:`, `fix(ci):`, `ci:`; CI/`docker.yml` publicam no GHCR em push para `main` e tags `v*`.
- `.vscode/launch.json` tem F5 prontos (servidor e pytest) apontando para `app_build/.venv`.
- Docs detalhadas: `app_build/README.md` (API, config/env, arquitetura) — consultar antes de mudar contratos.
