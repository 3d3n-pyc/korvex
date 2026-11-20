CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    list_id TEXT NOT NULL REFERENCES lists (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    due TEXT,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS tasks_by_list ON tasks (list_id, done, position);
CREATE INDEX IF NOT EXISTS tasks_by_due ON tasks (due) WHERE due IS NOT NULL;
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
