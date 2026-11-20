-- Local test-provider ledger. The path under test does not open this database.
-- One SQLite transaction in the receiver process is the version-fence boundary.
-- This is not cross-database atomicity and not a power-loss durability claim.

CREATE TABLE IF NOT EXISTS receiver_world (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS receiver_authority (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token INTEGER NOT NULL,
    epoch INTEGER NOT NULL,
    eligible INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS target_effects (
    effect_key TEXT PRIMARY KEY,
    payload BLOB NOT NULL,
    payload_hash TEXT NOT NULL,
    world_version INTEGER NOT NULL,
    authority_token INTEGER NOT NULL,
    authority_epoch INTEGER NOT NULL,
    receipt_id TEXT NOT NULL,
    logical_effect_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_attempts (
    attempt_id TEXT PRIMARY KEY,
    effect_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    outcome TEXT NOT NULL,
    world_version_observed INTEGER,
    expected_world_version INTEGER,
    fenced INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_conflicts (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    effect_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    existing_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
