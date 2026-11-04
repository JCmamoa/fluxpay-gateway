# Spring Payment Gateway

## Login credentials

The login endpoint (`POST /auth/login`) accepts only the credentials configured
with the `AUTH_USERNAME` and `AUTH_PASSWORD` environment variables. The
application refuses to start if either value is missing. Passwords are encoded
with BCrypt in memory and are never included in the repository.

PowerShell example:

```powershell
$env:AUTH_USERNAME = "demo"
$env:AUTH_PASSWORD = "use-a-strong-local-password"
$env:JWT_SECRET = "replace-this-with-a-random-secret-of-at-least-32-bytes"
mvn spring-boot:run
```

Send a login request:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"demo","password":"use-a-strong-local-password"}'
```

Invalid credentials receive `401 Unauthorized`. Use a secret manager or
environment variables for deployment; do not commit credentials or production
JWT secrets.

## Create a payment

Use the `access_token` returned by login as a bearer token. A successful request
is saved to the in-memory H2 database and returns `201 Created`.

```powershell
$token = (Invoke-RestMethod -Method Post -Uri http://localhost:8080/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"demo","password":"use-a-strong-local-password"}').access_token

Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/payments `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body '{"payerId":"customer-1","payeeId":"merchant-1","amount":25.90,"currency":"BRL"}'
```

The primary and fallback gateways are simulated. Payment amounts must be at
least `0.01`; supported currencies are `BRL`, `USD`, and `EUR`.

## Metrics summary

The authenticated `GET /api/metrics/summary` endpoint requires a bearer token
with the `metrics` scope and returns transaction counts, success/fallback rates,
average latency, and total volume.

## Local load test

The k6 script targets only `http://localhost:8080`. Start the application first,
then run k6 with the same login credentials configured for the application:

```powershell
$env:AUTH_USERNAME = "demo"
$env:AUTH_PASSWORD = "use-a-strong-local-password"
k6 run .\load-tests\payment-load.js
```

The script runs a roughly five-minute ramping-VU scenario followed by a
ramping-arrival-rate stress scenario. Run it only against your local test
instance, not a production or third-party service.
