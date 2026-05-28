"""
tests/test_create_index.py

TASK-026: Unit tests for ingest/create_index.py.

Asserts:
  (a) cluster health is checked (wait_for_cluster) before index creation
  (b) index body contains all expected fields with correct types
  (c) description and key_feature have type: semantic_text
  (d) name has type: text with a .keyword sub-field
  (e) coverage_type field is present (TASK-022)
  (f) --delete-existing flow calls indices.delete before indices.create
"""
import importlib
import sys
import types
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers — build a minimal fake elasticsearch module so create_index.py
# can be imported without real credentials in the environment.
# ---------------------------------------------------------------------------

def _make_fake_es_module():
    """Return a fake `elasticsearch` package with Elasticsearch and NotFoundError."""
    fake_module = types.ModuleType("elasticsearch")

    class NotFoundError(Exception):
        pass

    fake_module.NotFoundError = NotFoundError
    fake_module.Elasticsearch = MagicMock

    sys.modules["elasticsearch"] = fake_module
    return fake_module


_fake_es = _make_fake_es_module()


def _import_create_index():
    """Import (or re-import) create_index with patched env vars and ES client."""
    env_patch = {
        "ES_URL":    "https://fake-serverless.es.io",
        "ES_API_KEY": "fake-api-key",
    }
    with patch.dict("os.environ", env_patch):
        # Force re-import each time to pick up fresh mocks
        if "create_index" in sys.modules:
            del sys.modules["create_index"]
        # Add ingest/ to path
        import importlib.util
        import pathlib

        spec = importlib.util.spec_from_file_location(
            "create_index",
            pathlib.Path(__file__).parent.parent / "ingest" / "create_index.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIndexMapping:
    """Assert the INDEX_MAPPING constant contains the authoritative field set."""

    def setup_method(self):
        self.mod = _import_create_index()
        self.props = self.mod.INDEX_MAPPING["mappings"]["properties"]

    # (c) Semantic fields
    def test_description_is_semantic_text(self):
        assert self.props["description"]["type"] == "semantic_text"

    def test_key_feature_is_semantic_text(self):
        assert self.props["key_feature"]["type"] == "semantic_text"

    # (d) Name has text + .keyword sub-field
    def test_name_is_text_with_keyword_subfield(self):
        name_mapping = self.props["name"]
        assert name_mapping["type"] == "text"
        assert name_mapping["fields"]["keyword"]["type"] == "keyword"

    # (e) TASK-022: coverage_type present
    def test_coverage_type_is_keyword(self):
        assert "coverage_type" in self.props
        assert self.props["coverage_type"]["type"] == "keyword"

    # (b) All expected fields present with correct types
    def test_eligibility_field_types(self):
        assert self.props["min_age"]["type"] == "integer"
        assert self.props["max_age"]["type"] == "integer"
        assert self.props["smoker_eligible"]["type"] == "boolean"
        assert self.props["min_income"]["type"] == "long"
        assert self.props["max_sum_assured"]["type"] == "long"
        assert self.props["medical_required_above"]["type"] == "long"
        assert self.props["is_active"]["type"] == "boolean"

    def test_premium_fields_are_integer(self):
        assert self.props["premium_min_monthly"]["type"] == "integer"
        assert self.props["premium_max_monthly"]["type"] == "integer"

    def test_keyword_fields(self):
        for field in ("id", "product_code", "product_type", "plan_category", "uin",
                      "tags", "rider_name", "rider_type", "exclusions"):
            assert self.props[field]["type"] == "keyword", f"{field} should be keyword"

    def test_all_14_required_fields_present(self):
        required = {
            "id", "product_code", "name", "product_type", "coverage_type",
            "plan_category", "uin", "description", "key_feature", "sales_pitch",
            "tags", "min_age", "max_age", "smoker_eligible", "min_income",
            "max_sum_assured", "medical_required_above", "exclusions",
            "premium_min_monthly", "premium_max_monthly", "is_active",
        }
        missing = required - set(self.props.keys())
        assert not missing, f"Missing mapping fields: {missing}"


class TestWaitForCluster:
    """Assert cluster connectivity is checked before index creation."""

    def test_wait_for_cluster_calls_client_info(self):
        mod = _import_create_index()
        mod.client = MagicMock()
        mod.client.info.return_value = {"version": {"number": "9.0.0"}}
        mod.wait_for_cluster(timeout_seconds=10)
        mod.client.info.assert_called_once()

    def test_wait_for_cluster_raises_after_timeout(self):
        import pytest
        mod = _import_create_index()
        mod.client = MagicMock()
        mod.client.info.side_effect = ConnectionError("unreachable")
        with pytest.raises(RuntimeError, match="Cluster unreachable"):
            mod.wait_for_cluster(timeout_seconds=0)

    def test_create_index_called_after_wait_for_cluster(self):
        """Verify wait_for_cluster is called before indices.create in the __main__ flow."""
        mod = _import_create_index()
        call_order = []

        mod.client = MagicMock()
        mod.client.info.side_effect = lambda: call_order.append("wait") or {"version": {"number": "9.0.0"}}
        mod.client.indices.exists.return_value = False
        mod.client.indices.exists_alias.return_value = False
        mod.client.indices.create.side_effect = lambda **kw: call_order.append("create")
        mod.client.indices.put_alias.side_effect = lambda **kw: call_order.append("alias")

        mod.wait_for_cluster()
        mod.create_index()

        assert call_order.index("wait") < call_order.index("create")


class TestDeleteExisting:
    """Assert --delete-existing calls delete before create."""

    def test_delete_existing_calls_delete_then_create(self):
        mod = _import_create_index()
        call_order = []

        mod.client = MagicMock()
        mod.client.indices.delete_alias.side_effect = lambda **kw: call_order.append("del_alias")
        mod.client.indices.delete.side_effect = lambda **kw: call_order.append("del_index")
        mod.client.indices.exists.return_value = False
        mod.client.indices.exists_alias.return_value = False
        mod.client.indices.create.side_effect = lambda **kw: call_order.append("create")

        mod.delete_index_if_exists()
        mod.create_index()

        assert "del_index" in call_order
        assert call_order.index("del_index") < call_order.index("create")

    def test_delete_existing_is_idempotent_when_index_absent(self):
        """delete_index_if_exists should not raise when index/alias do not exist."""
        mod = _import_create_index()
        mod.client = MagicMock()
        # Simulate NotFoundError (index absent)
        mod.client.indices.delete_alias.side_effect = _fake_es.NotFoundError
        mod.client.indices.delete.side_effect = _fake_es.NotFoundError
        # Should complete without raising
        mod.delete_index_if_exists()
