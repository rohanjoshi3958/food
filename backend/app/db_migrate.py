from sqlalchemy import text

from app.database import engine

MIGRATIONS = [
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS store_name VARCHAR",
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS analysis_status VARCHAR DEFAULT 'pending'",
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS analysis_error TEXT",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS receipt_id VARCHAR REFERENCES receipts(id) ON DELETE SET NULL",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS store_item_name VARCHAR",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS serving_size VARCHAR",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS calories DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS protein_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS carbs_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS fat_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS fiber_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS sodium_mg DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS nutrition_notes TEXT",
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS draft_items JSONB",
]


def run_migrations() -> None:
    with engine.begin() as connection:
        for statement in MIGRATIONS:
            connection.execute(text(statement))

        connection.execute(
            text(
                "UPDATE receipts SET analysis_status = 'completed' "
                "WHERE analysis_status IS NULL"
            )
        )
