from sqlalchemy import text

from app.database import engine

MIGRATIONS = [
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS store_name VARCHAR",
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS analysis_status VARCHAR DEFAULT 'pending'",
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS analysis_error TEXT",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS receipt_id VARCHAR REFERENCES receipts(id) ON DELETE SET NULL",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS store_item_name VARCHAR",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS serving_size VARCHAR",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS servings_per_container DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS original_quantity VARCHAR",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS calories DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS protein_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS carbs_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS fat_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS fiber_g DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS sodium_mg DOUBLE PRECISION",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS nutrition_notes TEXT",
    "ALTER TABLE ingredients ADD COLUMN IF NOT EXISTS unit_warning TEXT",
    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS draft_items JSONB",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS ingredients_used TEXT",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS instructions TEXT",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS photo_filename VARCHAR",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS meal_id VARCHAR REFERENCES meals(id) ON DELETE SET NULL",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS description TEXT",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS photo_filename VARCHAR",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS ingredients_used_data JSONB",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS calories DOUBLE PRECISION",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS protein_g DOUBLE PRECISION",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS carbs_g DOUBLE PRECISION",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS fat_g DOUBLE PRECISION",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS fiber_g DOUBLE PRECISION",
    "ALTER TABLE meals ADD COLUMN IF NOT EXISTS sodium_mg DOUBLE PRECISION",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS calories DOUBLE PRECISION",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS protein_g DOUBLE PRECISION",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS carbs_g DOUBLE PRECISION",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS fat_g DOUBLE PRECISION",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS fiber_g DOUBLE PRECISION",
    "ALTER TABLE cookbook_entries ADD COLUMN IF NOT EXISTS sodium_mg DOUBLE PRECISION",
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        id VARCHAR PRIMARY KEY,
        user_id VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
        token_hash VARCHAR NOT NULL UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_auth_sessions_token_hash ON auth_sessions (token_hash)",
    """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id VARCHAR PRIMARY KEY,
        user_id VARCHAR NOT NULL REFERENCES "User"(id) ON DELETE CASCADE,
        token_hash VARCHAR NOT NULL UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_token_hash ON password_reset_tokens (token_hash)",
]


def run_migrations() -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        for statement in MIGRATIONS:
            connection.execute(text(statement))

        connection.execute(
            text(
                "UPDATE receipts SET analysis_status = 'completed' "
                "WHERE analysis_status IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE ingredients SET original_quantity = quantity "
                "WHERE original_quantity IS NULL AND quantity IS NOT NULL"
            )
        )
