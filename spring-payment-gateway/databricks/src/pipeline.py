import argparse
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from delta.tables import DeltaTable
from pyspark import StorageLevel
from pyspark.sql import Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from source_contract import canonicalize_api_record, validate_api_source


LOGGER = logging.getLogger("payment_analytics")
RAW_SCHEMA = T.StructType(
    [
        T.StructField("source_system", T.StringType(), False),
        T.StructField("source_record_id", T.StringType(), False),
        T.StructField("ingested_at", T.TimestampType(), False),
        T.StructField("run_id", T.StringType(), False),
        T.StructField("payload_json", T.StringType(), False),
        T.StructField("payload_sha256", T.StringType(), False),
    ]
)
PAYMENT_PAYLOAD_SCHEMA = T.StructType(
    [
        T.StructField("transaction_id", T.StringType()),
        T.StructField("amount", T.StringType()),
        T.StructField("currency", T.StringType()),
        T.StructField("status", T.StringType()),
        T.StructField("gateway_used", T.StringType()),
        T.StructField("latency_ms", T.StringType()),
        T.StructField("created_at", T.StringType()),
    ]
)
SETTLEMENT_PAYLOAD_SCHEMA = T.StructType(
    [
        T.StructField("transaction_id", T.StringType()),
        T.StructField("amount", T.StringType()),
        T.StructField("currency", T.StringType()),
        T.StructField("settlement_date", T.StringType()),
    ]
)
def _table(catalog, layer, name):
    return f"{catalog}.{layer}.{name}"


def _ensure_table(spark, name, schema):
    if not spark.catalog.tableExists(name):
        spark.createDataFrame([], schema).write.format("delta").mode("errorifexists").saveAsTable(name)


def _secret(scope, key):
    from databricks.sdk.runtime import dbutils

    return dbutils.secrets.get(scope=scope, key=key)


def _load_api_sources(scope, key):
    if not key:
        return []

    sources = json.loads(_secret(scope, key))
    if not isinstance(sources, list):
        raise ValueError("A configuração de APIs deve ser um array JSON")

    names = set()
    for source in sources:
        validate_api_source(source)
        if source["name"] in names:
            raise ValueError(f"Nome de fonte de API duplicado: {source['name']}")
        names.add(source["name"])
    return sources


def _append_raw(spark, table_name, records):
    dataframe = spark.createDataFrame(records, RAW_SCHEMA)
    dataframe.write.format("delta").mode("append").saveAsTable(table_name)
    return len(records)


def _database_records(spark, args, run_id):
    payment_table = _table(args.catalog, "bronze", "payments_raw")
    _ensure_table(spark, payment_table, RAW_SCHEMA)
    query = (
        "SELECT id, transaction_id, amount, currency, status, gateway_used, "
        "latency_ms, created_at FROM transactions"
    )
    jdbc_url = _secret(args.secret_scope, args.jdbc_url_secret_key)
    jdbc_user = _secret(args.secret_scope, args.jdbc_user_secret_key)
    jdbc_password = _secret(args.secret_scope, args.jdbc_password_secret_key)

    source = (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("query", query)
        .option("user", jdbc_user)
        .option("password", jdbc_password)
        .option("driver", "org.postgresql.Driver")
        .load()
    )
    payload_columns = [
        F.col("transaction_id"),
        F.col("amount").cast("string").alias("amount"),
        F.col("currency"),
        F.col("status"),
        F.col("gateway_used"),
        F.col("latency_ms").cast("string").alias("latency_ms"),
        F.col("created_at").cast("string").alias("created_at"),
    ]
    raw = (
        source.select(
            F.lit("postgres_transactions").alias("source_system"),
            F.col("id").cast("string").alias("source_record_id"),
            F.to_json(F.struct(*payload_columns)).alias("payload_json"),
        )
        .withColumn("ingested_at", F.current_timestamp())
        .withColumn("run_id", F.lit(run_id))
        .withColumn("payload_sha256", F.sha2("payload_json", 256))
        .select(*RAW_SCHEMA.fieldNames())
        .persist(StorageLevel.MEMORY_AND_DISK)
    )
    count = raw.count()
    raw.write.format("delta").mode("append").saveAsTable(payment_table)
    raw.unpersist()
    return count


def _request_api_page(source, token, page_value, page_number):
    params = dict(source.get("query_params", {}))
    page_param = source.get("page_param", "page")
    size_param = source.get("page_size_param", "page_size")
    if page_param:
        params[page_param] = page_value if page_value is not None else page_number
    if size_param:
        params[size_param] = source.get("page_size", 500)
    separator = "&" if urllib.parse.urlparse(source["url"]).query else "?"
    url = source["url"]
    if params:
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(
                    f"API {source['name']} retornou HTTP {error.code}"
                ) from error
            retry_after = error.headers.get("Retry-After")
            delay = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
            time.sleep(min(delay, 30))
        except urllib.error.URLError as error:
            if attempt == 2:
                raise RuntimeError(f"Falha de rede ao consultar a API {source['name']}") from error
            time.sleep(2**attempt)

    raise RuntimeError(f"Não foi possível consultar a API {source['name']}")


def _api_records(source, token, run_id):
    records_path = source.get("items_path", "")
    next_page_path = source.get("next_page_path")
    page_size = source.get("page_size", 500)
    page_value = None

    for page_number in range(1, 1001):
        response = _request_api_page(source, token, page_value, page_number)
        items = response
        if records_path:
            for segment in records_path.strip("$.").split("."):
                if segment:
                    items = items.get(segment) if isinstance(items, dict) else None
        if not isinstance(items, list):
            raise ValueError(f"A resposta da API {source['name']} não contém uma lista de registros")

        ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
        page_records = []
        for item in items:
            payload = canonicalize_api_record(item, source["field_map"], source["record_type"])
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            page_records.append(
                (
                    source["name"],
                    payload["source_record_id"],
                    ingested_at,
                    run_id,
                    payload_json,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                )
            )
        yield page_records

        if next_page_path:
            next_value = response
            for segment in next_page_path.strip("$.").split("."):
                if segment:
                    next_value = next_value.get(segment) if isinstance(next_value, dict) else None
            if next_value in (None, ""):
                break
            if not isinstance(next_value, (str, int, float)):
                raise ValueError(f"O cursor de paginação da API {source['name']} deve ser escalar")
            page_value = next_value
        elif len(items) < page_size:
            break
        else:
            page_value = None
        if page_number == 1000:
            raise RuntimeError(f"API {source['name']} excedeu o limite de 1000 páginas")

def _ingest_apis(spark, args, run_id, sources):
    counts = {}
    tables = {
        "payments": _table(args.catalog, "bronze", "payments_raw"),
        "settlements": _table(args.catalog, "bronze", "settlements_raw"),
    }
    for source in sources:
        token = _secret(args.secret_scope, source["token_secret_key"])
        table_name = tables[source["record_type"]]
        _ensure_table(spark, table_name, RAW_SCHEMA)
        source_count = 0
        for records in _api_records(source, token, run_id):
            source_count += _append_raw(spark, table_name, records)
        counts[source["name"]] = source_count
        LOGGER.info(
            "Ingestão API concluída: source=%s type=%s rows=%d run_id=%s",
            source["name"],
            source["record_type"],
            source_count,
            run_id,
        )
    return counts


def ingest(spark, args):
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    for layer in ("bronze", "silver", "gold"):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{args.catalog}`.`{layer}`")

    api_sources = _load_api_sources(args.secret_scope, args.api_sources_secret_key)
    payment_count = _database_records(spark, args, run_id)
    api_counts = _ingest_apis(spark, args, run_id, api_sources)
    audit_schema = T.StructType(
        [
            T.StructField("run_id", T.StringType(), False),
            T.StructField("source_system", T.StringType(), False),
            T.StructField("record_type", T.StringType(), False),
            T.StructField("rows_ingested", T.LongType(), False),
            T.StructField("started_at", T.TimestampType(), False),
            T.StructField("completed_at", T.TimestampType(), False),
        ]
    )
    audit_rows = [
        (
            run_id,
            "postgres_transactions",
            "payments",
            payment_count,
            started_at.replace(tzinfo=None),
            datetime.now(timezone.utc).replace(tzinfo=None),
        )
    ]
    for source in api_sources:
        audit_rows.append(
            (
                run_id,
                source["name"],
                source["record_type"],
                api_counts[source["name"]],
                started_at.replace(tzinfo=None),
                datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
    audit_table = _table(args.catalog, "bronze", "ingestion_audit")
    _ensure_table(spark, audit_table, audit_schema)
    spark.createDataFrame(audit_rows, audit_schema).write.format("delta").mode("append").saveAsTable(audit_table)
    LOGGER.info(
        "Ingestão finalizada: run_id=%s postgres_rows=%d api_sources=%d",
        run_id,
        payment_count,
        len(api_sources),
    )


def _quality_reason(dataframe, record_type):
    reasons = [
        F.when(
            F.col("transaction_id").isNull() | (F.trim("transaction_id") == ""),
            F.lit("transaction_id ausente"),
        ),
        F.when(F.col("amount").isNull(), F.lit("valor ausente ou inválido")),
        F.when(
            F.col("amount_raw").isNull() | ~F.col("amount_raw").rlike(r"^-?\d+(\.\d{1,2})?$"),
            F.lit("valor deve ter até duas casas decimais"),
        ),
        F.when(F.col("currency").isNull() | ~F.col("currency").isin("BRL", "USD", "EUR"), F.lit("moeda inválida")),
    ]
    if record_type == "payments":
        reasons.extend(
            [
                F.when(F.col("amount") <= 0, F.lit("valor deve ser positivo")),
                F.when(
                    F.col("status").isNull() | ~F.col("status").isin("APPROVED", "FAILED", "PENDING"),
                    F.lit("status inválido"),
                ),
                F.when(F.col("created_at").isNull(), F.lit("created_at ausente ou inválido")),
                F.when(F.col("latency_ms") < 0, F.lit("latency_ms negativo")),
            ]
        )
    else:
        reasons.extend(
            [
                F.when(
                    F.col("source_record_id").isNull() | (F.trim("source_record_id") == ""),
                    F.lit("source_record_id ausente"),
                ),
                F.when(F.col("settlement_date").isNull(), F.lit("settlement_date ausente ou inválido")),
            ]
        )
    return dataframe.withColumn("quality_reason", F.concat_ws("; ", *reasons))


def _merge_current(spark, source, table_name, keys):
    condition = " AND ".join(f"target.`{key}` = incoming.`{key}`" for key in keys)
    DeltaTable.forName(spark, table_name).alias("target").merge(
        source.alias("incoming"), condition
    ).whenMatchedUpdateAll(
        condition="incoming.ingested_at >= target.ingested_at"
    ).whenNotMatchedInsertAll().execute()


def _transform_payments(spark, catalog, run_id):
    raw_table = _table(catalog, "bronze", "payments_raw")
    silver_table = _table(catalog, "silver", "payments")
    quarantine_table = _table(catalog, "silver", "data_quality_quarantine")
    _ensure_table(spark, raw_table, RAW_SCHEMA)
    current_run_raw = spark.table(raw_table).filter(F.col("run_id") == run_id)
    parsed = current_run_raw.withColumn("_payload", F.from_json("payload_json", PAYMENT_PAYLOAD_SCHEMA)).select(
        "source_system",
        "source_record_id",
        F.col("_payload.transaction_id").alias("transaction_id"),
        F.col("_payload.amount").alias("amount_raw"),
        F.expr("try_cast(_payload.amount AS DECIMAL(19,2))").alias("amount"),
        F.upper(F.trim(F.col("_payload.currency"))).alias("currency"),
        F.upper(F.trim(F.col("_payload.status"))).alias("status"),
        F.col("_payload.gateway_used").alias("gateway_used"),
        F.expr("try_cast(_payload.latency_ms AS BIGINT)").alias("latency_ms"),
        F.expr("try_cast(_payload.created_at AS TIMESTAMP)").alias("created_at"),
        "ingested_at",
        "run_id",
        "payload_sha256",
    )
    checked = _quality_reason(parsed, "payments")
    invalid = checked.filter(F.col("quality_reason") != "")
    valid = checked.filter(F.col("quality_reason") == "").drop("quality_reason", "amount_raw")
    _ensure_table(spark, silver_table, valid.schema)

    quarantine_schema = T.StructType(
        RAW_SCHEMA.fields
        + [
            T.StructField("record_type", T.StringType(), False),
            T.StructField("quality_reason", T.StringType(), False),
        ]
    )
    _ensure_table(spark, quarantine_table, quarantine_schema)
    invalid_raw = (
        invalid.select("source_system", "source_record_id", "ingested_at", "run_id", "payload_sha256", "quality_reason")
        .join(
            current_run_raw.select(
                "source_system", "source_record_id", "ingested_at", "run_id", "payload_json", "payload_sha256"
            ),
            ["source_system", "source_record_id", "ingested_at", "run_id", "payload_sha256"],
        )
        .withColumn("record_type", F.lit("payments"))
        .select(*quarantine_schema.fieldNames())
    )
    invalid_raw.write.format("delta").mode("append").saveAsTable(quarantine_table)

    window = Window.partitionBy("transaction_id").orderBy(
        F.col("ingested_at").desc(), F.col("run_id").desc()
    )
    current_batch = valid.withColumn("_rank", F.row_number().over(window)).filter(F.col("_rank") == 1).drop("_rank")
    _merge_current(spark, current_batch, silver_table, ["transaction_id"])
    return silver_table


def _transform_settlements(spark, catalog, run_id):
    raw_table = _table(catalog, "bronze", "settlements_raw")
    silver_table = _table(catalog, "silver", "settlements")
    _ensure_table(spark, raw_table, RAW_SCHEMA)
    current_run_raw = spark.table(raw_table).filter(F.col("run_id") == run_id)
    parsed = current_run_raw.withColumn(
        "_payload", F.from_json("payload_json", SETTLEMENT_PAYLOAD_SCHEMA)
    ).select(
        "source_system",
        "source_record_id",
        F.col("_payload.transaction_id").alias("transaction_id"),
        F.col("_payload.amount").alias("amount_raw"),
        F.expr("try_cast(_payload.amount AS DECIMAL(19,2))").alias("amount"),
        F.upper(F.trim(F.col("_payload.currency"))).alias("currency"),
        F.col("ingested_at"),
        F.col("run_id"),
        F.col("payload_sha256"),
        F.expr("try_cast(_payload.settlement_date AS TIMESTAMP)").alias("settlement_date"),
    )
    checked = _quality_reason(parsed, "settlements")
    invalid = checked.filter(F.col("quality_reason") != "")
    valid = checked.filter(F.col("quality_reason") == "").drop("quality_reason", "amount_raw")
    _ensure_table(spark, silver_table, valid.schema)

    quarantine_table = _table(catalog, "silver", "data_quality_quarantine")
    _ensure_table(
        spark,
        quarantine_table,
        T.StructType(
            RAW_SCHEMA.fields
            + [
                T.StructField("record_type", T.StringType(), False),
                T.StructField("quality_reason", T.StringType(), False),
            ]
        ),
    )
    invalid_raw = (
        invalid.select("source_system", "source_record_id", "ingested_at", "run_id", "payload_sha256", "quality_reason")
        .join(
            current_run_raw.select(
                "source_system", "source_record_id", "ingested_at", "run_id", "payload_json", "payload_sha256"
            ),
            ["source_system", "source_record_id", "ingested_at", "run_id", "payload_sha256"],
        )
        .withColumn("record_type", F.lit("settlements"))
        .select(
            "source_system",
            "source_record_id",
            "ingested_at",
            "run_id",
            "payload_json",
            "payload_sha256",
            "record_type",
            "quality_reason",
        )
    )
    invalid_raw.write.format("delta").mode("append").saveAsTable(quarantine_table)

    current_batch = valid.withColumn(
        "_rank",
        F.row_number().over(
            Window.partitionBy("source_system", "source_record_id").orderBy(
                F.col("ingested_at").desc(), F.col("run_id").desc()
            )
        ),
    ).filter(F.col("_rank") == 1).drop("_rank")
    _merge_current(spark, current_batch, silver_table, ["source_system", "source_record_id"])
    return silver_table


def _write_snapshot(dataframe, table_name):
    dataframe.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(table_name)


def _build_executive_report(spark, catalog, run_id):
    payments = spark.table(_table(catalog, "silver", "payments"))
    report = (
        payments.withColumn("payment_date", F.to_date("created_at"))
        .groupBy("payment_date", "currency")
        .agg(
            F.count("*").alias("total_transactions"),
            F.sum(F.when(F.col("status") != "PENDING", 1).otherwise(0)).alias("resolved_transactions"),
            F.sum(F.when(F.col("status") == "APPROVED", 1).otherwise(0)).alias("approved_transactions"),
            F.sum(F.when(F.col("status") == "FAILED", 1).otherwise(0)).alias("failed_transactions"),
            F.sum(F.when(F.col("status") == "PENDING", 1).otherwise(0)).alias("pending_transactions"),
            F.sum(F.when(F.col("status") == "APPROVED", F.col("amount")).otherwise(F.lit(Decimal("0.00"))))
            .cast(T.DecimalType(19, 2))
            .alias("approved_amount"),
            F.round(
                F.avg(F.when(F.col("status") != "PENDING", F.col("latency_ms"))), 2
            ).alias("average_latency_ms"),
        )
        .withColumn(
            "approval_rate_percent",
            F.when(
                F.col("resolved_transactions") > 0,
                F.round(F.col("approved_transactions") * 100.0 / F.col("resolved_transactions"), 2),
            ),
        )
        .withColumn("report_generated_at", F.current_timestamp())
        .withColumn("run_id", F.lit(run_id))
        .select(
            "payment_date",
            "currency",
            "total_transactions",
            "resolved_transactions",
            "approved_transactions",
            "failed_transactions",
            "pending_transactions",
            "approval_rate_percent",
            "approved_amount",
            "average_latency_ms",
            "report_generated_at",
            "run_id",
        )
    )
    _write_snapshot(report, _table(catalog, "gold", "executive_daily"))


def _build_reconciliation(spark, catalog, run_id, settlement_source_configured):
    payments = (
        spark.table(_table(catalog, "silver", "payments"))
        .filter(F.col("status") == "APPROVED")
        .select(
            F.col("transaction_id").alias("payment_transaction_id"),
            F.col("created_at").alias("payment_created_at"),
            F.col("amount").alias("payment_amount"),
            F.col("currency").alias("payment_currency"),
        )
        .alias("p")
    )
    settlements = spark.table(_table(catalog, "silver", "settlements")).alias("s")
    joined = payments.join(
        settlements,
        F.col("p.payment_transaction_id") == F.col("s.transaction_id"),
        "left",
    )
    grouped = joined.groupBy(
        F.col("p.payment_transaction_id"),
        F.col("p.payment_created_at"),
        F.col("p.payment_amount"),
        F.col("p.payment_currency"),
    ).agg(
        F.count(F.col("s.source_record_id")).alias("settlement_record_count"),
        F.countDistinct(F.col("s.currency")).alias("settlement_currency_count"),
        F.sort_array(F.collect_set(F.col("s.currency"))).alias("_settlement_currencies"),
        F.sum(
            F.when(F.col("s.currency") == F.col("p.payment_currency"), F.col("s.amount"))
        ).cast(T.DecimalType(19, 2)).alias("settled_amount"),
        F.max(F.col("s.settlement_date")).alias("last_settled_at"),
    )
    status = (
        F.when(F.lit(not settlement_source_configured), F.lit("PENDING_SOURCE"))
        .when(F.col("settlement_record_count") == 0, F.lit("NOT_FOUND"))
        .when(F.col("settlement_currency_count") > 1, F.lit("CURRENCY_MISMATCH"))
        .when(
            F.size(
                F.array_except(
                    F.col("_settlement_currencies"),
                    F.array(F.col("payment_currency")),
                )
            ) > 0,
            F.lit("CURRENCY_MISMATCH"),
        )
        .when(F.col("settled_amount") != F.col("payment_amount"), F.lit("AMOUNT_MISMATCH"))
        .otherwise(F.lit("RECONCILED"))
    )
    result = (
        grouped.withColumn("settlement_status", status)
        .withColumn(
            "settlement_difference",
            (F.col("settled_amount") - F.col("payment_amount")).cast(T.DecimalType(19, 2)),
        )
        .withColumn("payment_date", F.to_date("payment_created_at"))
        .withColumn("settlement_currencies", F.concat_ws(",", F.col("_settlement_currencies")))
        .withColumn("report_generated_at", F.current_timestamp())
        .withColumn("run_id", F.lit(run_id))
        .select(
            F.col("payment_transaction_id").alias("transaction_id"),
            "payment_date",
            F.col("payment_currency").alias("currency"),
            "payment_amount",
            "settled_amount",
            "settlement_difference",
            "settlement_status",
            "settlement_record_count",
            "settlement_currencies",
            "last_settled_at",
            "report_generated_at",
            "run_id",
        )
    )
    _write_snapshot(result, _table(catalog, "gold", "settlement_reconciliation"))


def _publish_csv_reports(spark, catalog):
    base_path = f"/Volumes/{catalog}/gold/executive_reports"
    reports = [
        (
            "executive_daily",
            spark.table(_table(catalog, "gold", "executive_daily")),
        ),
        (
            "settlement_exceptions",
            spark.table(_table(catalog, "gold", "settlement_reconciliation")).filter(
                F.col("settlement_status") != "RECONCILED"
            ),
        ),
    ]
    for report_name, dataframe in reports:
        (
            dataframe.write.mode("overwrite")
            .option("header", "true")
            .option("encoding", "UTF-8")
            .csv(f"{base_path}/{report_name}")
        )


def transform(spark, args):
    for layer in ("bronze", "silver", "gold"):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{args.catalog}`.`{layer}`")

    api_sources = _load_api_sources(args.secret_scope, args.api_sources_secret_key)
    audit_table = _table(args.catalog, "bronze", "ingestion_audit")
    if not spark.catalog.tableExists(audit_table):
        raise RuntimeError("Não há execução de ingestão registrada; execute a tarefa ingest primeiro")
    latest_run = (
        spark.table(audit_table)
        .orderBy(F.col("completed_at").desc())
        .select("run_id")
        .first()
    )
    if latest_run is None:
        raise RuntimeError("Não há execução de ingestão registrada; execute a tarefa ingest primeiro")
    run_id = latest_run["run_id"]
    _transform_payments(spark, args.catalog, run_id)
    _transform_settlements(spark, args.catalog, run_id)
    _build_executive_report(spark, args.catalog, run_id)
    _build_reconciliation(
        spark,
        args.catalog,
        run_id,
        settlement_source_configured=any(
            source["record_type"] == "settlements" for source in api_sources
        ),
    )
    _publish_csv_reports(spark, args.catalog)
    LOGGER.info("Camadas silver/gold atualizadas: run_id=%s", run_id)


def _arguments():
    parser = argparse.ArgumentParser(description="Pipeline Medallion do gateway de pagamentos")
    parser.add_argument("--task", choices=("ingest", "transform"), required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--secret-scope", required=True)
    parser.add_argument("--jdbc-url-secret-key", default="")
    parser.add_argument("--jdbc-user-secret-key", default="")
    parser.add_argument("--jdbc-password-secret-key", default="")
    parser.add_argument("--api-sources-secret-key", default="")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", args.catalog):
        parser.error("--catalog deve conter apenas letras, números e sublinhados")
    return args


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _arguments()
    spark = SparkSession.builder.getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "America/Sao_Paulo")
    if args.task == "ingest":
        ingest(spark, args)
    else:
        transform(spark, args)


if __name__ == "__main__":
    from pyspark.sql import SparkSession

    main()
