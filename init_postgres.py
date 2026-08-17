"""Initialize Vera Spa PostgreSQL schema.

Run after DB/Cloud SQL is reachable and environment variables are set:
    python init_postgres.py
"""
from pathlib import Path
from sqlalchemy import text
import vera_postgres as vpg


def main():
    engine = vpg.get_engine()
    sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    ok, msg = vpg.healthcheck()
    print(msg)
    if not ok:
        raise SystemExit(1)
    print(f"Initialized {len(statements)} SQL statements.")


if __name__ == "__main__":
    main()
