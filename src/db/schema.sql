PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Children and their delivery recipients (src/db/children.py). Product state
-- belongs to a child; a recipient is a channel address. No rows = single-child
-- mode (CHILD_BIRTH_DATE, VK_PEER_ID from the environment).
CREATE TABLE children (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE recipients (
    id INTEGER PRIMARY KEY,
    child_id INTEGER NOT NULL REFERENCES children(id),
    channel TEXT NOT NULL CHECK(channel IN ('vk', 'telegram')),
    address TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(child_id, channel, address)
);

-- Self-service onboarding in the product Telegram bot (src/bot/product_bot.py).
CREATE TABLE invites (
    id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    used_by TEXT
);
CREATE TABLE members (
    chat_id TEXT PRIMARY KEY,
    invite_id INTEGER REFERENCES invites(id),
    display_name TEXT,
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE onboarding (
    chat_id TEXT PRIMARY KEY REFERENCES members(chat_id),
    step TEXT NOT NULL CHECK(step IN ('name', 'birth_date', 'confirm')),
    name TEXT,
    birth_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    child_id INTEGER REFERENCES children(id)
);

CREATE INDEX idx_games_date ON games(game_date);
CREATE UNIQUE INDEX idx_games_child_date ON games(child_id, game_date) WHERE child_id IS NOT NULL;

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
    external_event_id TEXT,
    raw_text TEXT NOT NULL,
    received_at TEXT,
    analysis_json TEXT,
    processed INTEGER NOT NULL DEFAULT 0,
    analyzed_at TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_channel_external_event_id
ON feedback(channel, external_event_id)
WHERE external_event_id IS NOT NULL;

CREATE TABLE migration_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

INSERT INTO migration_meta(key, value) VALUES ('schema_version', '3');
