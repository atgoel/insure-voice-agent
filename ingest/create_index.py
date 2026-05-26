"""
create_index.py
Creates the Elasticsearch insurance_products index with ELSER semantic_text mapping.
Run this ONCE before ingesting data.

Usage: python create_index.py
Requires: ES_URL and ES_API_KEY environment variables
"""
import os
from elasticsearch import Elasticsearch

ES_URL = os.environ["ES_URL"]
ES_API_KEY = os.environ["ES_API_KEY"]
INDEX_NAME = "insurance_products"
INFERENCE_ID = "elser-v2-endpoint"

client = Elasticsearch(ES_URL, api_key=ES_API_KEY)


def create_elser_inference_endpoint():
    """Create ELSER v2 inference endpoint if it doesn't exist."""
    try:
        client.inference.get(inference_id=INFERENCE_ID)
        print(f"Inference endpoint '{INFERENCE_ID}' already exists.")
    except Exception:
        print(f"Creating ELSER inference endpoint '{INFERENCE_ID}'...")
        client.inference.put(
            inference_id=INFERENCE_ID,
            body={
                "service": "elasticsearch",
                "service_settings": {
                    "adaptive_allocations": {
                        "enabled": True,
                        "min_number_of_allocations": 1,
                        "max_number_of_allocations": 4
                    },
                    "num_threads": 1,
                    "model_id": ".elser_model_2"
                }
            }
        )
        print("ELSER inference endpoint created.")


def create_index():
    """Create the insurance_products index with ELSER semantic_text mapping."""
    if client.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists. Delete it first to recreate.")
        return

    print(f"Creating index '{INDEX_NAME}'...")
    client.indices.create(
        index=INDEX_NAME,
        body={
            "mappings": {
                "properties": {
                    "id":                     {"type": "keyword"},
                    "name":                   {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "product_type":           {"type": "keyword"},
                    "description":            {"type": "semantic_text", "inference_id": INFERENCE_ID},
                    "min_age":                {"type": "integer"},
                    "max_age":                {"type": "integer"},
                    "smoker_eligible":        {"type": "boolean"},
                    "min_income":             {"type": "long"},
                    "max_sum_assured":        {"type": "long"},
                    "medical_required_above": {"type": "long"},
                    "exclusions":             {"type": "keyword"},
                    "coverage_type":          {"type": "keyword"},
                    "premium_min_monthly":    {"type": "integer"},
                    "premium_max_monthly":    {"type": "integer"}
                }
            }
        }
    )
    print(f"Index '{INDEX_NAME}' created successfully.")


if __name__ == "__main__":
    create_elser_inference_endpoint()
    create_index()
