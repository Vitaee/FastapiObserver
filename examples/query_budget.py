"""Run: python examples/query_budget.py (requires SQLAlchemy)."""

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from fastapiobserver.db_queries import count_queries
from fastapiobserver.testing import assert_query_budget


def main() -> None:
    engine = create_engine("sqlite://")
    count_queries(engine)
    try:
        with assert_query_budget(engine, 2) as budget:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                try:
                    connection.execute(text("SELECT * FROM absent_table"))
                except SQLAlchemyError:
                    pass
        assert budget.count == 2
        print("2 native cursor attempts, including one failed execution")
        try:
            with assert_query_budget(engine, 0):
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
        except AssertionError:
            print("Over-budget query detected")
        else:
            raise AssertionError("Expected the budget to reject an execution")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
