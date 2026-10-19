PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS observations (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  listing_id TEXT NOT NULL,
  source TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_listing ON observations(listing_id, observed_at);
CREATE TABLE IF NOT EXISTS saved_searches (id TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookmarks (listing_id TEXT PRIMARY KEY, saved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, state_json TEXT NOT NULL, updated_at TEXT NOT NULL);
