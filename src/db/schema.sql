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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_test INTEGER NOT NULL DEFAULT 0
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
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    blocked_at TEXT
);
CREATE TABLE onboarding (
    chat_id TEXT PRIMARY KEY REFERENCES members(chat_id),
    step TEXT NOT NULL CHECK(step IN ('name', 'birth_date', 'confirm')),
    name TEXT,
    birth_date TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Owner-facing events; written by the product bot, forwarded by the ops bot.
CREATE TABLE user_events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    chat_id TEXT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    notified_at TEXT
);

-- One row per model attempt in daily preparation (src/ops/llm_report.py).
CREATE TABLE model_runs (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    target_date TEXT NOT NULL,
    child_id INTEGER,
    attempt INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('pass', 'rejected', 'error')),
    backend TEXT,
    model TEXT,
    wall_ms INTEGER,
    api_ms INTEGER,
    num_turns INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    web_searches INTEGER,
    cost_usd REAL,
    errors_json TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0
);

-- Warm reserve of validated games per age (src/preparation/fallback.py).
CREATE TABLE reserve_games (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    age_months INTEGER NOT NULL,
    skill TEXT NOT NULL,
    title TEXT NOT NULL,
    mechanics TEXT,
    source_name TEXT,
    source_url TEXT NOT NULL,
    message_text TEXT NOT NULL,
    candidate_json TEXT,
    made_by TEXT,
    mechanic_tag TEXT,
    UNIQUE(title, age_months)
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
    child_id INTEGER REFERENCES children(id),
    origin TEXT NOT NULL DEFAULT 'model',
    origin_reason TEXT,
    candidate_json TEXT,
    mechanic_tag TEXT
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
    insights_at TEXT,
    insights_status TEXT,
    reply_sent_at TEXT,
    FOREIGN KEY(game_id) REFERENCES games(id)
);

-- Wishes about the service (src/feedback/insights.py, /idea in the product bot).
CREATE TABLE wishes (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL CHECK(source IN ('feedback', 'command')),
    feedback_id INTEGER,
    child_id INTEGER,
    chat_id TEXT,
    text TEXT NOT NULL,
    evidence TEXT
);

-- Sunday digests sent (src/ops/weekly_digest.py).
CREATE TABLE digests (
    child_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    week TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (child_id, channel, address, week)
);

-- Feedback insights (src/feedback/insights.py).
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    feedback_id INTEGER NOT NULL REFERENCES feedback(id),
    child_id INTEGER,
    game_id INTEGER,
    kind TEXT NOT NULL CHECK(kind IN ('outcome', 'difficulty', 'skill', 'attention', 'like', 'dislike')),
    value TEXT NOT NULL,
    skill TEXT,
    evidence TEXT NOT NULL
);
CREATE TABLE child_profiles (
    child_id INTEGER PRIMARY KEY,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    profile_json TEXT NOT NULL
);


CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_channel_external_event_id
ON feedback(channel, external_event_id)
WHERE external_event_id IS NOT NULL;

CREATE TABLE migration_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE channel_links (
    id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    from_channel TEXT NOT NULL CHECK(from_channel IN ('vk', 'telegram')),
    from_address TEXT NOT NULL,
    to_channel TEXT NOT NULL CHECK(to_channel IN ('vk', 'telegram')),
    to_address TEXT,
    status TEXT NOT NULL DEFAULT 'issued'
        CHECK(status IN ('issued', 'requested', 'approved', 'rejected', 'replaced')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    requested_at TEXT,
    decided_at TEXT
);
CREATE TABLE swaps (
    id INTEGER PRIMARY KEY,
    child_id INTEGER NOT NULL REFERENCES children(id),
    game_id INTEGER NOT NULL REFERENCES games(id),
    game_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    original_json TEXT NOT NULL,
    replacement_source TEXT,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(child_id, game_date)
);
-- "Удалить наши данные" (src/bot/erasure.py): the family's rows leave the live tables for an
-- archive kept ERASURE_GRACE_DAYS, so the family can come back; then the archive is purged
-- and the address is kept only as a hash (for the counts). erasure_addresses: every chat of
-- the family, so a message from any of them finds the archive.
CREATE TABLE erasures (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirming'
        CHECK(status IN ('confirming', 'archived', 'restored', 'purged')),
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    purge_after TEXT,
    closed_at TEXT,
    children INTEGER NOT NULL DEFAULT 0,
    survey TEXT,
    archive_json TEXT
);
CREATE TABLE erasure_addresses (
    erasure_id INTEGER NOT NULL REFERENCES erasures(id),
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    PRIMARY KEY (channel, address)
);
-- Why families leave: one optional question after "удалить". Not linked to a child or a
-- chat, so it outlives the family's data; the free text follows the feedback-text term.
CREATE TABLE exit_surveys (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel TEXT NOT NULL,
    reason TEXT NOT NULL,
    text TEXT,
    days_used INTEGER
);
INSERT INTO migration_meta(key, value) VALUES ('schema_version', '15');

-- One-time request to star the project on GitHub (src/ops/weekly_digest.py).
CREATE TABLE star_requests (
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    child_id INTEGER,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel, address)
);

-- VK self-service onboarding, outbox and inbound VK events (src/bot/vk_dialog.py).
CREATE TABLE applications (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    child_id INTEGER REFERENCES children(id)
);
CREATE INDEX idx_applications_pending ON applications(channel, address) WHERE status = 'pending';
CREATE TABLE outbox (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE inbound_events (
    channel TEXT NOT NULL,
    event_id TEXT NOT NULL,
    address TEXT NOT NULL,
    reply TEXT,
    handled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel, event_id)
);
