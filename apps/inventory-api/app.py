from fastapi import FastAPI

app = FastAPI(title="Inventory API")


@app.get("/")
def home():
    return {
        "service": "inventory-api",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "service": "inventory-api",
        "status": "healthy"
    }


@app.get("/inventory")
def inventory():
    return {
        "items": [
            {
                "id": 1,
                "name": "Laptop",
                "quantity": 10
            },
            {
                "id": 2,
                "name": "Keyboard",
                "quantity": 25
            },
            {
                "id": 3,
                "name": "Mouse",
                "quantity": 50
            }
        ]
    }
