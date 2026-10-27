# FluxPay — Gateway de Pagamento Simplificado

[![CI](https://github.com/JCmamoa/fluxpay-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/JCmamoa/fluxpay-gateway/actions/workflows/ci.yml)
[![Docker](https://github.com/JCmamoa/fluxpay-gateway/actions/workflows/docker.yml/badge.svg)](https://github.com/JCmamoa/fluxpay-gateway/actions/workflows/docker.yml)
[![Versão](https://img.shields.io/badge/vers%C3%A3o-v0.2-blueviolet)](specs/payment-gateway.md)

Gateway de pagamento de demonstração construído pelo time autônomo: **@pm → @engineer → @qa → @devops**.

## Funcionalidades

- Pagamentos via **PIX**, **cartão de crédito** e **cartão de débito** (provedores simulados, determinísticos)
- Processamento **assíncrono** com fila de mensageria durável (outbox em banco, at-least-once)
- **Idempotência** de ponta a ponta (`Idempotency-Key` + CAS) — zero cobrança dupla
- **Webhooks** assinados (HMAC-SHA256) com retry/backoff exponencial + jitter e DLQ
- Console de demonstração web (criar pagamento, acompanhar status, simular PIX, inspecionar webhooks)
- Empacotamento **Docker** (multi-stage, non-root, healthcheck) + execução nativa Windows

## Rodar

### Nativo (Windows)

```powershell
cd app_build
.\run.ps1
```

- Console: `http://localhost:8000/`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

### Docker

```powershell
cd app_build
docker compose up --build
```

### Credenciais demo

| Item | Valor |
| --- | --- |
| API Key | `sk_demo_fluxpay_2026` |
| Webhook Secret | `whsec_demo_fluxpay_2026` |

### Testes

```powershell
cd app_build
.\.venv\Scripts\python.exe -m pytest -q
```

## Estrutura

```
app_build/                  → aplicação completa (código, testes, Docker, README detalhado)
specs/payment-gateway.md    → especificação técnica aprovada (v0.2)
.opencode/agents/           → time de agents (pm, engineer, qa, devops)
```

## Segurança

Ambiente de demonstração: provedores simulados, dados fictícios, PAN/CVV nunca persistidos, API keys hasheadas. **Não é apto a produção real** (ver spec, seções 3.2 e 11).

Detalhes completos: `app_build/README.md`.
