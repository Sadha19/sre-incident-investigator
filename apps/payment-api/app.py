from fastapi import FastAPI

app = FastAPI(title="Payment API")


@app.get("/")
def home():
    return {
        "service": "payment-api",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "service": "payment-api",
        "status": "healthy"
    }


@app.post("/payment")
def make_payment():
    return {
        "payment": "successful",
        "transaction_id": "TXN-1001"
    }
