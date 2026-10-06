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
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    quantity INTEGER NOT NULL,
                    price NUMERIC(10, 2) NOT NULL
                )
            """)

            cursor.execute("SELECT COUNT(*) AS count FROM products")
            result = cursor.fetchone()

            if result["count"] == 0:
                cursor.executemany(
                    """
                    INSERT INTO products (name, quantity, price)
                    VALUES (%s, %s, %s)
                    """,
                    [
                        ("Laptop", 10, 50000),
                        ("Keyboard", 25, 2000),
                        ("Mouse", 50, 800),
                    ],
                )

        conn.commit()


def get_products():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, quantity, price
                FROM products
                ORDER BY id
            """)

            return cursor.fetchall()
