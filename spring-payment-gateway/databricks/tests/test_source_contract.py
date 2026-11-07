import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from source_contract import canonicalize_api_record, validate_api_source


class ApiSourceContractTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "name": "settlement_api",
            "record_type": "settlements",
            "url": "https://settlement.example.com/v1/records",
            "token_secret_key": "settlement-token",
            "items_path": "data.items",
            "field_map": {
                "source_record_id": "id",
                "transaction_id": "payment.transaction_id",
                "amount": "net_amount",
                "currency": "currency",
                "settlement_date": "settled_at",
            },
        }

    def test_accepts_https_settlement_source_with_allowlisted_fields(self):
        validate_api_source(self.source)

    def test_rejects_credentials_embedded_in_api_url(self):
        self.source["url"] = "https://user:password@settlement.example.com/v1/records"
        with self.assertRaisesRegex(ValueError, "sem credenciais embutidas"):
            validate_api_source(self.source)

    def test_rejects_api_tokens_in_query_parameters(self):
        self.source["query_params"] = {"api_key": "do-not-store-here"}
        with self.assertRaisesRegex(ValueError, "não por query string"):
            validate_api_source(self.source)

    def test_rejects_sensitive_or_unknown_mapped_fields(self):
        self.source["field_map"]["card_number"] = "card.number"
        with self.assertRaisesRegex(ValueError, "campos não permitidos"):
            validate_api_source(self.source)

    def test_rejects_missing_required_reconciliation_fields(self):
        del self.source["field_map"]["transaction_id"]
        with self.assertRaisesRegex(ValueError, "campos obrigatórios"):
            validate_api_source(self.source)

    def test_canonicalizes_only_the_configured_allowlist(self):
        record = {
            "id": 17,
            "payment": {"transaction_id": "TXN-1"},
            "net_amount": "10.00",
            "currency": "BRL",
            "settled_at": "2026-11-07T10:00:00Z",
            "card": {"number": "4111111111111111"},
        }
        result = canonicalize_api_record(record, self.source["field_map"], "settlements")
        self.assertEqual(result["source_record_id"], "17")
        self.assertEqual(result["transaction_id"], "TXN-1")
        self.assertNotIn("card", result)

    def test_rejects_records_missing_required_fields(self):
        record = {"id": 17, "net_amount": "10.00", "currency": "BRL", "settled_at": "2026-11-07"}
        with self.assertRaisesRegex(ValueError, "campos obrigatórios"):
            canonicalize_api_record(record, self.source["field_map"], "settlements")


if __name__ == "__main__":
    unittest.main()
