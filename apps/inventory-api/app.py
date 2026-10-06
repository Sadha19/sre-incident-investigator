import json
import os

import redis
from fastapi import FastAPI

from database import initialize_database, get_products


app = FastAPI(title="Inventory API")


REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_TTL = int(os.getenv("REDIS_TTL", "60"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
)


@app.on_event("startup")
def startup():
    initialize_database()


@app.get("/")
def home():
    return {
        "service": "inventory-api",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "service": "inventory-api",
        "status": "healthy",
    }


@app.get("/ready")
def ready():
    try:
        redis_client.ping()

        # Verify PostgreSQL is reachable
        get_products()

        return {
            "status": "ready",
            "redis": "connected",
            "postgres": "connected",
        }

    except Exception as e:
        return {
            "status": "not_ready",
            "error": str(e),
        }


@app.get("/inventory")
def inventory():

    cache_key = "inventory:products"

    # 1. Check Redis
    cached_data = redis_client.get(cache_key)

    if cached_data:
        print("INFO: Redis cache HIT")
        return {
            "source": "redis",
            "items": json.loads(cached_data),
        }

    # 2. Redis cache miss → PostgreSQL
    print("INFO: Redis cache MISS")
    print("INFO: Querying PostgreSQL")

    products = get_products()

    # Convert database rows to JSON-compatible data
    products = [
        {
            "id": product["id"],
            "name": product["name"],
            "quantity": product["quantity"],
            "price": float(product["price"]),
        }
        for product in products
    ]

    # 3. Store result in Redis
    redis_client.setex(
        cache_key,
        REDIS_TTL,
        json.dumps(products),
    )

    print("INFO: Inventory cached in Redis")

    return {
        "source": "postgresql",
        "items": products,
    }
