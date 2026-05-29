"""
create_index.py
Creates the Elasticsearch insurance_products index with ELSER semantic_text mapping.
Run this ONCE before ingesting data.

Uses a versioned index + alias pattern:
  - Real index:  insurance_products_v1
  - Alias:       insurance_products_current  (use this in all queries and ingests)

On Elastic Cloud Serverless, semantic_text uses the built-in inference endpoint
automatically — no inference_id or endpoint setup required.

To upgrade the mapping: create insurance_products_v2, reindex, then flip the alias
using PUT insurance_products_current/_alias/insurance_products_v2 — zero downtime.

Usage:
  python create_index.py                  # create index + alias (idempotent)
  python create_index.py --delete-existing  # delete then recreate (dev only)

Requires: ES_URL and ES_API_KEY environment variables
"""
import argparse
import os
import time
from elasticsearch import Elasticsearch, NotFoundError

ES_URL = os.environ["ES_URL"]
ES_API_KEY = os.environ["ES_API_KEY"]
INDEX_NAME = "insurance_products_v1"
ALIAS_NAME = "insurance_products_current"

# All 14 authoritative product fields + lifecycle flag
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            # Identity & codes
            "id":                     {"type": "keyword"},
            "product_code":           {"type": "keyword"},
            # name: text for BM25 match queries + .keyword for exact/sort/agg
            "name":                   {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "product_type":           {"type": "keyword"},
            "plan_category":          {"type": "keyword"},
            "uin":                    {"type": "keyword"},
            # Semantic fields — Serverless resolves built-in inference endpoint automatically
            "description":            {"type": "semantic_text"},
            "key_feature":            {"type": "semantic_text"},
            "sales_pitch":            {"type": "text"},
            "tags":                   {"type": "keyword"},
            # Rider (optional)
            "rider_name":             {"type": "keyword"},
            "rider_type":             {"type": "keyword"},
            # Eligibility constraints (used by compliance engine)
            "min_age":                {"type": "integer"},
            "max_age":                {"type": "integer"},
            "smoker_eligible":        {"type": "boolean"},
            "min_income":             {"type": "long"},
            "max_sum_assured":        {"type": "long"},
            "medical_required_above": {"type": "long"},
            "exclusions":             {"type": "keyword"},
            # Premium (flat — never nested)
            "premium_min_monthly":    {"type": "integer"},
            "premium_max_monthly":    {"type": "integer"},
            # Lifecycle
            "is_active":              {"type": "boolean"},
        }
    }
}

client = Elasticsearch(ES_URL, api_key=ES_API_KEY)


def wait_for_cluster(timeout_seconds: int = 30) -> None:
    """Wait for the Elasticsearch cluster to be reachable (TASK-024 readiness gate).

    On Elastic Cloud Serverless, the cluster is always available once credentials
    are valid. This check guards against transient connectivity issues on cold start.
    Raises RuntimeError if the cluster is unreachable after timeout_seconds.
    """
    deadline = time.time() + timeout_seconds
    last_exc: Exception = RuntimeError("Cluster not reached")
    while time.time() < deadline:
        try:
            info = client.info()
            print(f"Cluster reachable — version: {info['version']['number']}")
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"Waiting for cluster... ({exc})")
            time.sleep(5)
    raise RuntimeError(f"Cluster unreachable after {timeout_seconds}s: {last_exc}") from last_exc


def delete_index_if_exists() -> None:
    """Delete the versioned index and its alias. Used with --delete-existing (dev only)."""
    try:
        client.indices.delete_alias(index=INDEX_NAME, name=ALIAS_NAME)
        print(f"Alias '{ALIAS_NAME}' deleted.")
    except NotFoundError:
        pass
    try:
        client.indices.delete(index=INDEX_NAME)
        print(f"Index '{INDEX_NAME}' deleted.")
    except NotFoundError:
        pass


def create_index() -> None:
    """Create the versioned insurance_products index with the authoritative mapping."""
    if client.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists. Use --delete-existing to recreate.")
        return

    print(f"Creating index '{INDEX_NAME}'...")
    client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
    print(f"Index '{INDEX_NAME}' created successfully.")


def create_alias() -> None:
    """Point ALIAS_NAME to INDEX_NAME.

    All application code (queries, ingests) must use ALIAS_NAME, never INDEX_NAME directly.
    """
    if client.indices.exists_alias(name=ALIAS_NAME):
        print(f"Alias '{ALIAS_NAME}' already exists.")
        return

    client.indices.put_alias(index=INDEX_NAME, name=ALIAS_NAME)
    print(f"Alias '{ALIAS_NAME}' → '{INDEX_NAME}' created.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the insurance_products Elasticsearch index.")
    parser.add_argument(
        "--delete-existing",
        action="store_true",
        help="Delete and recreate the index (development use only — destroys all indexed data).",
    )
    args = parser.parse_args()

    wait_for_cluster()
    if args.delete_existing:
        delete_index_if_exists()
    create_index()
    create_alias()
