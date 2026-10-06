import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field

from database import initialize_database, create_payment


app = FastAPI(title="Payment API")


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)


@app.on_event("startup")
def startup():
    initialize_database()


@app.get("/")
def home():
    return {
        "service": "payment-api",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "service": "payment-api",
        "status": "healthy",
    }


@app.get("/ready")
def ready():
    try:
        # Simple database connectivity check
        from database import get_connection

        with get_connection() as conn:
            conn.execute("SELECT 1")

        return {
            "status": "ready",
            "postgres": "connected",
        }

    except Exception as e:
        return {
            "status": "not_ready",
            "error": str(e),
        }


@app.post("/payment")
def make_payment(payment: PaymentRequest):

    transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"

    result = create_payment(
        transaction_id=transaction_id,
        amount=payment.amount,
    )

    return {
        "payment": "successful",
        "transaction_id": result["transaction_id"],
        "amount": float(result["amount"]),
        "status": result["status"],
    }
