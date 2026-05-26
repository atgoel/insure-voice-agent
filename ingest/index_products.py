"""
index_products.py
Bulk ingests insurance_products.json into Elasticsearch.
ELSER inference is applied automatically via the semantic_text field pipeline.

Usage: python index_products.py
Requires: ES_URL and ES_API_KEY environment variables
"""
import json
import os
from pathlib import Path
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

ES_URL = os.environ["ES_URL"]
ES_API_KEY = os.environ["ES_API_KEY"]
INDEX_NAME = "insurance_products"
DATA_FILE = Path(__file__).parent.parent / "data" / "insurance_products.json"

client = Elasticsearch(ES_URL, api_key=ES_API_KEY)


def load_products():
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def generate_actions(products):
    for product in products:
        yield {
            "_index": INDEX_NAME,
            "_id": product["id"],
            "_source": product
        }


if __name__ == "__main__":
    products = load_products()
    print(f"Loaded {len(products)} products from {DATA_FILE}")

    success, errors = bulk(client, generate_actions(products), raise_on_error=False)
    print(f"Indexed: {success} documents")
    if errors:
        print(f"Errors: {errors}")
