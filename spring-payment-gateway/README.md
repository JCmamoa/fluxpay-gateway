# Spring Payment Gateway

This project is a payment API foundation. The payment gateway is simulated in
the `dev` and `test` profiles. **It does not charge real money and is not ready
to process production payments until a real `PaymentGateway` adapter has been
implemented, reviewed, and tested with the chosen provider.**

## Local development

Java 17 and Maven are required. The default profile is `dev`, which uses an
in-memory H2 payment database, a simulated gateway, and RabbitMQ for recovery
notifications. Configure the local services, administrator, and a
non-production JWT secret before starting:

```powershell
$env:DB_USER = "payments"
$env:DB_PASSWORD = "replace-with-a-local-password"
$env:PROFILE_DB_USER = "profiles"
$env:PROFILE_DB_PASSWORD = "replace-with-another-local-password"
$env:RABBITMQ_USER = "gateway"
$env:RABBITMQ_PASSWORD = "replace-with-a-rabbitmq-password"
docker compose up -d postgres private-profiles rabbitmq

$env:JWT_SECRET = "local-only-secret-with-at-least-32-bytes"
$env:BOOTSTRAP_ADMIN_USERNAME = "admin"
$env:BOOTSTRAP_ADMIN_PASSWORD = "change-this-local-password"
$env:PROFILE_DB_JDBC_URL = "jdbc:postgresql://localhost:5433/privateprofiles?sslmode=disable"
$env:RABBITMQ_USER = "gateway"
$env:RABBITMQ_PASSWORD = "replace-with-a-rabbitmq-password"
mvn spring-boot:run
```

The bootstrap administrator is created only when the username does not already
exist. Its password is BCrypt-hashed. For later access, provision and manage
users through the admin API; changing the bootstrap environment variables does
not overwrite an existing account.

## Production database and configuration

Production uses PostgreSQL, Flyway migrations, pooled R2DBC connections, and
requires explicit database credentials, bootstrap admin credentials, and a
JWT secret. Example local PostgreSQL setup with Docker Compose:

```powershell
$env:DB_USER = "payments"
$env:DB_PASSWORD = "replace-with-a-strong-database-password"
docker compose up -d postgres

$env:DB_URL = "r2dbc:postgresql://localhost:5432/paymentsdb"
$env:DB_JDBC_URL = "jdbc:postgresql://localhost:5432/paymentsdb"
$env:BOOTSTRAP_ADMIN_USERNAME = "admin"
$env:BOOTSTRAP_ADMIN_PASSWORD = "replace-with-a-strong-admin-password"
$env:JWT_SECRET = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
$env:SPRING_PROFILES_ACTIVE = "prod"
mvn spring-boot:run
```

For the local Compose database only, override both URLs to disable TLS on the
loopback connection:

```powershell
$env:DB_URL = "r2dbc:postgresql://localhost:5432/paymentsdb?sslMode=DISABLE"
$env:DB_JDBC_URL = "jdbc:postgresql://localhost:5432/paymentsdb?sslmode=disable"
```

Do not commit these values. Use a managed secret store, TLS-enabled database
connections, restricted network access, backups, and a tested secret-rotation
procedure in a real deployment. Database schema changes for production are
versioned under `src/main/resources/db/migration`.

The `prod` profile intentionally does not load the simulated gateway. Startup
will fail until an actual `PaymentGateway` and notification delivery adapter
are configured. No provider has been selected, and this repository does not
contain payment provider credentials or a real-money integration.

## Private profiles, recovery, messaging, and assistant

The private profile database is separate from the payment database. In
development it defaults to a dedicated H2 database; in production it must use
its own PostgreSQL database and credentials via `PROFILE_DB_JDBC_URL`,
`PROFILE_DB_USER`, and `PROFILE_DB_PASSWORD`. It stores profile contact details
and password-recovery records. Login identities and password hashes remain in
the application authentication database.

Local services can be started with Docker Compose:

```powershell
$env:DB_USER = "payments"
$env:DB_PASSWORD = "replace-with-a-local-password"
$env:PROFILE_DB_USER = "profiles"
$env:PROFILE_DB_PASSWORD = "replace-with-another-local-password"
$env:RABBITMQ_USER = "gateway"
$env:RABBITMQ_PASSWORD = "replace-with-a-rabbitmq-password"
docker compose up -d postgres private-profiles rabbitmq

$env:PROFILE_DB_JDBC_URL = "jdbc:postgresql://localhost:5433/privateprofiles?sslmode=disable"
$env:PROFILE_DB_USER = "profiles"
$env:PROFILE_DB_PASSWORD = "replace-with-another-local-password"
$env:RABBITMQ_USER = "gateway"
$env:RABBITMQ_PASSWORD = "replace-with-a-rabbitmq-password"
mvn spring-boot:run
```

An administrator can add a user's name and at least one recovery contact with
`PUT /api/users/{username}/profile`. `POST /auth/password-recovery/request`
accepts `{"username":"...","channel":"EMAIL"}` or `"SMS"` and always returns a
generic acknowledgement. Codes expire after ten minutes, are stored as BCrypt
hashes, and allow at most five verification attempts. A successful reset
invalidates existing access tokens. Recovery requests are limited per username
and client address in each application instance.

The `dev` profile publishes recovery notifications to RabbitMQ. Its simulated
consumer places messages in a bounded in-memory mailbox; an administrator can
inspect it at `GET /api/dev/notifications` to exercise the flow locally. The
`test` profile uses an in-memory publisher. Neither profile sends real email or
SMS. The mailbox endpoint is not present in production. A shared, distributed
rate limiter, durable recovery outbox, and real email/SMS providers are still
required before production.

`POST /api/support/assistant` provides a deterministic FAQ for payment and
account questions. It does not call an external AI, persist the conversation,
or support human handoff. Do not send passwords, recovery codes, or payment
details in the message. The same-origin chat interface is available at
`http://localhost:8080/assistant.html` while the application is running.

## Authentication and user administration

- `POST /auth/login` authenticates database-backed users and returns a
  15-minute JWT.
- `POST /auth/logout` revokes the presented token; subsequent requests reject it.
- The bootstrap administrator has `payments`, `metrics`, and `users:write`
  scopes. Regular users receive `payments` and `metrics`.
- `POST /api/users` creates a regular user and requires `users:write`.
- `PATCH /api/users/{username}/enabled` enables/disables a user. Disabled users'
  tokens are rejected immediately.
- `PUT /api/users/{username}/password` changes a user's password.
- Five login attempts per username are allowed per 15-minute window; further
  attempts return `429` with `Retry-After`. This limiter is per application
  instance, so multi-instance production deployments must also enforce a
  shared limit at the API gateway or edge.
- Passwords use BCrypt and are limited to 72 UTF-8 bytes to avoid BCrypt's
  silent input truncation.

Passwords must be at least 12 characters when creating or changing an account.
The JWT secret must be at least 32 bytes.

## Payments and idempotency

`POST /api/payments` requires a bearer token with the `payments` scope and an
`Idempotency-Key` header (1-128 characters):

```powershell
$token = (Invoke-RestMethod -Method Post -Uri http://localhost:8080/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"change-this-local-password"}').access_token

Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/payments `
  -Headers @{ Authorization = "Bearer $token"; "Idempotency-Key" = "order-123" } `
  -ContentType "application/json" `
  -Body '{"payerId":"customer-1","payeeId":"merchant-1","amount":25.90,"currency":"BRL"}'
```

The key is scoped to the authenticated user. Repeating it with the same request
returns the original transaction; reusing it for different payment data returns
`409 Conflict`. A request already in progress also returns `409`. The request
record is reserved before calling a gateway to avoid duplicate processing.
Real-provider adapters must also forward a stable idempotency identifier and
support reconciliation for transactions left `PENDING` after a crash.

Supported currencies are `BRL`, `USD`, and `EUR`. Payment amounts must be at
least `0.01`.

## Metrics and health

- `GET /api/metrics/summary` requires the `metrics` scope. Volume is reported
  separately by currency and includes approved payments only; amounts in
  different currencies are never added.
- `GET /actuator/health` and `GET /actuator/prometheus` are exposed for health
  checks and scraping.

## Tests and local load test

```powershell
mvn test
```

The k6 script targets only `http://localhost:8080` and uses environment
credentials. Start the local app first, then run:

```powershell
$env:AUTH_USERNAME = "admin"
$env:AUTH_PASSWORD = "change-this-local-password"
k6 run .\load-tests\payment-load.js
```

Run load tests only against a local test instance, not a production or
third-party service.
