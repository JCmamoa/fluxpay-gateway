# Analytics Lakehouse no Databricks

Implementação inicial da camada analítica do gateway. A aplicação continua
transacional em PostgreSQL; o job Databricks lê uma cópia analítica e não altera
o processamento de pagamentos.

## Escopo e limites conhecidos

- A fonte conectada hoje é `transactions` no PostgreSQL. O job lê somente
  `id`, `transaction_id`, `amount`, `currency`, `status`, `gateway_used`,
  `latency_ms` e `created_at`; não extrai identificadores de pagador/recebedor,
  chaves de idempotência ou hashes da requisição.
- O gateway atual não tem endpoint GET de pagamentos nem tabela de liquidação.
  Por isso, a reconciliação começa em `PENDING_SOURCE`. Não há números
  operacionais incluídos neste repositório.
- APIs adicionais são configuráveis por contrato REST GET e token Bearer.
  Configure a API real de liquidação antes de interpretar `NOT_FOUND`,
  `AMOUNT_MISMATCH` ou `RECONCILED` como resultado financeiro.
- `transactions` não tem `updated_at`. A ingestão faz snapshot completo diário
  para capturar mudanças de `PENDING` para `APPROVED` ou `FAILED`; essa escolha
  favorece correção sobre custo. Para tabelas grandes, implementar CDC ou
  adicionar um timestamp de atualização confiável antes de trocar por carga
  incremental.
- Valores são `DECIMAL(19,2)`, sem soma entre moedas. A reconciliação compara
  valores exatos em centavos e não aplica tolerância ou regra de tarifa não
  aprovada pelo negócio.

## Arquitetura e tabelas

O Asset Bundle provisiona um job diário às 07:00 (`America/Sao_Paulo`), com
dependência de ingestão antes das transformações:

1. **Bronze / Delta Lake** — snapshots JSON allowlisted, hash SHA-256, fonte,
   identificador de origem, horário de ingestão e `run_id`.
   - `bronze.payments_raw`
   - `bronze.settlements_raw`
   - `bronze.ingestion_audit`
2. **Silver / Delta Lake** — parsing tipado, deduplicação por transação ou
   registro de liquidação, atualização do estado corrente por `MERGE` e
   quarentena de dados inválidos.
   - `silver.payments`
   - `silver.settlements`
   - `silver.data_quality_quarantine`
3. **Gold / Delta Lake** — agregado diário executivo por moeda e reconciliação
   pagamento aprovado x liquidação.
   - `gold.executive_daily`
   - `gold.settlement_reconciliation`

O job atualiza ainda os diretórios CSV de entrega no volume UC
`/Volumes/payment_analytics/gold/executive_reports/`:

- `executive_daily/` — volume, contagens, taxa de aprovação e latência.
- `settlement_exceptions/` — detalhe por transação com status de conciliação.

Os CSVs são a entrega corrente (sobrescritos a cada execução); as tabelas Delta
são a fonte versionada do Power BI/Excel. Configure retenção e políticas de
acesso do catálogo de acordo com o ambiente.

## Implantação

Pré-requisitos: workspace Databricks com Unity Catalog, CLI Databricks com
suporte a Asset Bundles, PostgreSQL acessível pela rede do workspace e um
principal de serviço para o job.

1. Em um SQL Warehouse/console com privilégios administrativos, execute
   `sql/bootstrap.sql` (ajuste `payment_analytics` se usar outro catálogo).
2. Conceda ao principal de serviço do job `USE CATALOG`, `USE SCHEMA`,
   `CREATE TABLE` nos três schemas e `READ VOLUME`/`WRITE VOLUME` no volume
   `gold.executive_reports`. Conceda ao consumidor do Power BI/Excel apenas
   acesso de leitura às views Gold.
3. Crie um secret scope Databricks (o mesmo `jdbc_secret_scope` usado pelo
   bundle) e adicione:
   - `payments-jdbc-url`: URL JDBC TLS do PostgreSQL, por exemplo
     `jdbc:postgresql://db.internal:5432/paymentsdb?sslmode=require`.
   - `payments-jdbc-user` e `payments-jdbc-password`: usuário de leitura
     restrito à tabela `transactions`.
   - opcionalmente, `payment-api-sources`: JSON de configuração das APIs.
4. Configure o catálogo e o tipo de instância para o provedor cloud do
   workspace em `databricks/databricks.yml` e
   `databricks/resources/payment_analytics.yml`. O tipo padrão
`m5d.large` é para AWS; troque-o em Azure/GCP. A partir da raiz do projeto:

   ```powershell
Set-Location .\databricks
databricks bundle validate -t dev -var="jdbc_secret_scope=SEU_SCOPE" -var="service_principal_name=APP_ID"
databricks bundle deploy -t dev -var="jdbc_secret_scope=SEU_SCOPE" -var="service_principal_name=APP_ID"
```

5. Execute `payment-analytics-medallion` uma vez e valide as tabelas e CSVs.
   As execuções seguintes ocorrem diariamente. O primeiro uso de
   `sql/semantic_views.sql` deve ser depois da primeira execução, quando as
   tabelas Gold já existirem.

Não grave tokens, senhas, URLs com credenciais ou dados de produção em Git.
Restrinja e monitore o acesso ao PostgreSQL, ao secret scope e aos CSVs. A
conexão deve usar TLS e rede privada sempre que disponível.

## Contrato de APIs opcionais

Guarde o JSON abaixo no secret `payment-api-sources`. Os caminhos em
`field_map` selecionam explicitamente os campos permitidos; o corpo original
da API nunca é persistido. O token de cada origem é buscado em outro secret do
mesmo scope. A URL deve ser HTTPS e não pode incluir credenciais.

```json
[
  {
    "name": "settlement_api",
    "record_type": "settlements",
    "url": "https://api.seu-provedor.example/v1/settlements",
    "token_secret_key": "settlement-api-token",
    "items_path": "data.items",
    "next_page_path": "meta.next_cursor",
    "page_param": "cursor",
    "page_size_param": "limit",
    "page_size": 500,
    "field_map": {
      "source_record_id": "id",
      "transaction_id": "payment.transaction_id",
      "amount": "payment.amount",
      "currency": "currency",
      "settlement_date": "settled_at"
    }
  }
]
```

`record_type` aceita `payments` ou `settlements`. Para pagamentos, os campos
obrigatórios são `source_record_id`, `transaction_id`, `amount`, `currency`,
`status` e `created_at`; para liquidações, são
`source_record_id`, `transaction_id`, `amount`, `currency` e
`settlement_date`. Campos opcionais suportados incluem `gateway_used` e
`latency_ms` em pagamentos. A paginação usa token/cursor no `next_page_path`
ou número de página quando esse caminho não está configurado. A API precisa
retornar uma lista no `items_path` e suportar autenticação Bearer.

Para ingestão de pagamentos por API, use os mesmos campos canônicos e mantenha
`transaction_id` compatível com o identificador do gateway usado pela fonte
de liquidação.

## Regras de negócio, qualidade e reconciliação

- `APPROVED`, `FAILED` e `PENDING` são os estados aceitos. A taxa de aprovação
  usa apenas transações resolvidas (`APPROVED` + `FAILED`); pendências aparecem
  separadamente e não reduzem artificialmente a taxa.
- `approved_amount` é somente de pagamentos aprovados e está sempre agrupado
  por moeda. BRL, USD e EUR não são somados entre si.
- Dados sem transação, valor decimal válido, moeda suportada ou estado
  reconhecido são enviados à quarentena, com motivo, hash e linhagem.
- Reconciliação agrega eventos de liquidação por transação/moeda e compara o
  valor configurado exato. Status: `PENDING_SOURCE` (fonte não configurada),
  `NOT_FOUND`, `CURRENCY_MISMATCH`, `AMOUNT_MISMATCH` ou `RECONCILED`.
- O campo de liquidação mapeado como `amount` deve ter a mesma semântica do
  valor do pagamento. Não configure conciliação de valor líquido pós-tarifa
  contra valor bruto sem aprovar e modelar tarifas, estornos e tolerâncias.
- Valores com mais de duas casas decimais são colocados em quarentena; não são
  arredondados silenciosamente antes da reconciliação.
- `bronze.ingestion_audit` registra volume e origem por execução; o `run_id`
  das tabelas Gold aponta para o snapshot ingerido que as atualizou.

## Power BI e Excel

Use o conector **Azure Databricks** no Power BI ou Excel e selecione o SQL
Warehouse, catálogo `payment_analytics` e as views:

- `gold.vw_executive_daily` para o relatório executivo.
- `gold.vw_settlement_exceptions` para pendências e divergências.

Para atualização automatizada no Power BI Service, configure credenciais do
warehouse e agendamento de refresh após a janela diária do job. O job também
publica os CSVs no volume Unity Catalog para consumidores que precisem de
arquivo. A governança, o refresh do Power BI Service e a distribuição externa
dos arquivos exigem configuração no workspace/tenant e não são simulados pelo
código deste repositório.

## Testes locais

Os testes do contrato da API usam somente a biblioteca padrão:

```powershell
python -m unittest discover -s databricks\tests -v
python -m py_compile databricks\src\pipeline.py databricks\src\source_contract.py
```

As transformações PySpark/Delta e os workflows devem ser validados no workspace
de desenvolvimento Databricks antes da promoção para produção.
