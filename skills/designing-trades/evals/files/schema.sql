-- Current schema of the game database (PostgreSQL 16, default settings).

CREATE TABLE players (
  id    bigserial PRIMARY KEY,
  name  text   NOT NULL UNIQUE,
  gold  bigint NOT NULL DEFAULT 0
);

CREATE TABLE items (
  id        bigserial PRIMARY KEY,
  kind      text   NOT NULL,                       -- 'sword', 'potion', ...
  owner_id  bigint NOT NULL REFERENCES players(id)
);

-- The API server (Python, psycopg 3, no ORM) opens one connection per request from a pool.
-- Clients talk JSON over HTTPS and retry failed calls up to three times.
