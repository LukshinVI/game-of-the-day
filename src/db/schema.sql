PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE games (
    id INTEGER PRIMARY KEY,
    legacy_id TEXT UNIQUE,
    game_date TEXT NOT NULL,
    age_months INTEGER,
    title TEXT NOT NULL,
    skill TEXT,
    mechanics TEXT,
    source_name TEXT,
    source_url TEXT,
    message_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_games_date ON games(game_date);

CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'vk',
    recipient TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    external_message_id TEXT,
    sent_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(id)
);

CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'vk',
    raw_text TEXT NOT NULL,
    received_at TEXT,
    analysis_json TEXT,
    processed INTEGER NOT NULL DEFAULT 0,
    analyzed_at TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id)
);

CREATE TABLE migration_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
