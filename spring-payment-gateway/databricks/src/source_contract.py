import re
from urllib.parse import parse_qsl, urlparse


ALLOWED_RECORD_TYPES = {"payments", "settlements"}
ALLOWED_FIELDS = {
    "source_record_id",
    "transaction_id",
    "amount",
    "currency",
    "status",
    "gateway_used",
    "latency_ms",
    "created_at",
    "settlement_date",
}
REQUIRED_FIELDS = {
    "payments": {
        "source_record_id",
        "transaction_id",
        "amount",
        "currency",
        "status",
        "created_at",
    },
    "settlements": {
        "source_record_id",
        "transaction_id",
        "amount",
        "currency",
        "settlement_date",
    },
}


def _is_credential_parameter(name):
    normalized = name.lower().replace("-", "_")
    return normalized == "key" or any(
        marker in normalized
        for marker in ("token", "secret", "password", "api_key", "access_key", "authorization")
    )


def validate_api_source(source):
    if not isinstance(source, dict):
        raise ValueError("Cada fonte de API deve ser um objeto JSON")

    name = source.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("A fonte de API precisa de um nome simples e único")

    record_type = source.get("record_type")
    if record_type not in ALLOWED_RECORD_TYPES:
        raise ValueError(f"Tipo de registro inválido para a fonte {name}")

    url = source.get("url")
    parsed_url = urlparse(url) if isinstance(url, str) else None
    if (
        parsed_url is None
        or parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.username
        or parsed_url.password
    ):
        raise ValueError(f"A fonte {name} deve usar uma URL HTTPS sem credenciais embutidas")
    if any(_is_credential_parameter(key) for key, _ in parse_qsl(parsed_url.query)):
        raise ValueError(f"A fonte {name} não pode enviar credenciais na query string")

    token_secret_key = source.get("token_secret_key")
    if not isinstance(token_secret_key, str) or not token_secret_key.strip():
        raise ValueError(f"A fonte {name} precisa informar token_secret_key")

    field_map = source.get("field_map")
    if not isinstance(field_map, dict):
        raise ValueError(f"A fonte {name} precisa informar field_map")

    unknown_fields = set(field_map) - ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError(
            f"A fonte {name} mapeia campos não permitidos: {', '.join(sorted(unknown_fields))}"
        )

    missing_fields = REQUIRED_FIELDS[record_type] - set(field_map)
    if missing_fields:
        raise ValueError(
            f"A fonte {name} não mapeia campos obrigatórios: {', '.join(sorted(missing_fields))}"
        )

    for field, path in field_map.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"O caminho JSON de {field} na fonte {name} é inválido")

    page_size = source.get("page_size", 500)
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 1000:
        raise ValueError(f"page_size da fonte {name} deve estar entre 1 e 1000")

    for path_key in ("items_path", "next_page_path", "page_param", "page_size_param"):
        value = source.get(path_key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{path_key} da fonte {name} deve ser texto")

    query_params = source.get("query_params", {})
    if not isinstance(query_params, dict) or not all(
        isinstance(key, str) and isinstance(value, (str, int, float))
        for key, value in query_params.items()
    ):
        raise ValueError(f"query_params da fonte {name} deve ser um objeto de valores simples")
    if any(_is_credential_parameter(key) for key in query_params):
        raise ValueError(f"A fonte {name} deve enviar credenciais por Bearer token, não por query string")


def extract_json_path(record, path):
    value = record
    for part in path.strip("$.").split("."):
        if not part:
            continue
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def canonicalize_api_record(record, field_map, record_type):
    canonical = {
        field: extract_json_path(record, path)
        for field, path in field_map.items()
    }

    missing_fields = [
        field for field in REQUIRED_FIELDS[record_type]
        if canonical.get(field) is None or canonical.get(field) == ""
    ]
    if missing_fields:
        raise ValueError(f"Registro da API sem campos obrigatórios: {', '.join(sorted(missing_fields))}")

    canonical["source_record_id"] = str(canonical["source_record_id"])
    canonical["transaction_id"] = str(canonical["transaction_id"])
    return canonical
