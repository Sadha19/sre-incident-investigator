import os
import psycopg
from psycopg.rows import dict_row


POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "sre_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "sre_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "sre_password")


def get_connection():
    return psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        row_factory=dict_row,
    )


def initialize_database():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    transaction_id VARCHAR(100) UNIQUE NOT NULL,
                    amount NUMERIC(10, 2) NOT NULL,
                    status VARCHAR(30) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()


def create_payment(transaction_id, amount):
    with get_connection() as conn:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO payments
                    (transaction_id, amount, status)
                VALUES
                    (%s, %s, %s)
                RETURNING
                    id,
                    transaction_id,
                    amount,
                    status,
                    created_at
                """,
                (transaction_id, amount, "SUCCESS"),
            )

            payment = cursor.fetchone()

        conn.commit()

        return payment
