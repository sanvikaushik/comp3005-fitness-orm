from sqlalchemy import text

from models import member, scheduling, equipment, payment  # noqa: F401
from models.base import Base, engine


def init_db() -> None:
    # Create all ORM tables
    Base.metadata.create_all(bind=engine)

    # Create trigger, view, and index
    with engine.begin() as conn:
        # 1) Trigger: keep track of when a member last logged a metric
        conn.execute(
            text(
                """
                ALTER TABLE member
                    ADD COLUMN IF NOT EXISTS last_metric_at TIMESTAMP;
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE room
                    ADD COLUMN IF NOT EXISTS primary_trainer_id INTEGER REFERENCES trainer(trainer_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE class_schedule
                    ADD COLUMN IF NOT EXISTS price NUMERIC(8,2) DEFAULT 50;
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE private_session
                    ADD COLUMN IF NOT EXISTS price NUMERIC(8,2) DEFAULT 75;
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE payment
                    ADD COLUMN IF NOT EXISTS private_session_id INTEGER REFERENCES private_session(session_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE billing_item
                    ADD COLUMN IF NOT EXISTS private_session_id INTEGER UNIQUE REFERENCES private_session(session_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE payment
                    ADD COLUMN IF NOT EXISTS private_session_id INTEGER REFERENCES private_session(session_id);
                """
            )
        )
        # Ensure the health_metric timestamp column always defaults to now()
        conn.execute(
            text(
                """
                ALTER TABLE health_metric
                    ALTER COLUMN "timestamp" SET DEFAULT NOW();
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION update_last_metric()
                RETURNS TRIGGER AS $$
                BEGIN
                    UPDATE member
                    SET last_metric_at = NEW.timestamp
                    WHERE member_id = NEW.member_id;

                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )

        conn.execute(
            text(
                """
                DROP TRIGGER IF EXISTS trg_update_member_last_metric ON health_metric;
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TRIGGER trg_update_member_last_metric
                AFTER INSERT ON health_metric
                FOR EACH ROW
                EXECUTE FUNCTION update_last_metric();
                """
            )
        )

        # 2) View: latest metric per member (used for dashboard/reporting)
        conn.execute(
            text(
                """
                CREATE OR REPLACE VIEW member_latest_metric_view AS
                SELECT
                    m.member_id,
                    m.first_name,
                    m.last_name,
                    m.email,
                    m.target_weight,
                    m.notes,
                    m.last_metric_at,
                    hm.weight,
                    hm.heart_rate,
                    hm.timestamp AS metric_timestamp
                FROM member m
                LEFT JOIN LATERAL (
                    SELECT *
                    FROM health_metric
                    WHERE health_metric.member_id = m.member_id
                    ORDER BY timestamp DESC
                    LIMIT 1
                ) hm ON TRUE;
                """
            )
        )

        # 3) Index: speed up lookups of metrics by member_id
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_health_metric_member_id
                ON health_metric(member_id);
                """
            )
        )


if __name__ == "__main__":
    init_db()
    print("Database tables + view + trigger + index created.")
