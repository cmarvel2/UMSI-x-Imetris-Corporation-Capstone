import os

import psycopg


def database_connection():
    return psycopg.connect(
        dbname="healthcare_edi",
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
