# Set up the database — step by step

Do this once. Everything the application needs is created by these steps;
nothing has been pre-made for you.

**Current state:** there is no database. `python main.py db status` reports
`exists: False`. Your 6 articles are safe in `export/`, and a SQL dump of
the previous database is at `~/clouddesk-db-backups/`.

---

## Before you start — what already exists on your machine

| Thing | State | Needed? |
|---|---|---|
| PostgreSQL 17.10 (Homebrew) | installed, running on 5432 | yes — the binaries |
| `pgvector` 0.8.6 | installed | yes — the vector column type |
| `psycopg` + `pgvector` (Python) | installed in `venv/` | yes — the driver |
| A database for this app | **does not exist** | you create it below |

If you want to install the first two yourself as well, remove them first:

```bash
brew uninstall pgvector
brew services stop postgresql@17 && brew uninstall postgresql@17
```

Then reinstall as part of Step 0. **Careful:** uninstalling
`postgresql@17` can take `/usr/local/var/postgresql@17` with it, which is
where your *other* application's `whistle_e2e_mob` database lives. Back
that up first if you go this route:

```bash
pg_dump -p 5432 -d whistle_e2e_mob -f ~/whistle_backup.sql
```

---

## Decide first: where should this database live?

Pick one. The rest of the guide branches only at Step 1.

**A — Its own server (recommended).** Separate port, separate data
directory, separate processes. Nothing shared with your other app, so
stopping or upgrading one never touches the other.

**B — Alongside your existing server.** A new database on the Postgres
already running on 5432. Less to manage, but the two apps share a server
lifecycle: `brew services stop postgresql@17` takes down both.

**C — A managed Postgres.** Neon, Supabase, RDS. Data lives off your
machine and survives a laptop rebuild.

---

## Step 0 — prerequisites

Only if you removed them above:

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17
```

Check the Python driver is present:

```bash
venv/bin/python -c "import psycopg, pgvector; print('driver ok')"
```

If it errors:

```bash
venv/bin/pip install "psycopg[binary]" pgvector
```

---

## Step 1 — create the server

### Option A — its own server

```bash
# 1. create a new cluster (a self-contained PostgreSQL data directory)
initdb -D ~/pgdata/clouddesk -U $USER --encoding=UTF8

# 2. start it on a port nothing else is using
pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start

# 3. confirm
pg_isready -p 5433              # expect: accepting connections
```

`initdb` creates the directory; it must not already exist. Pick any port
that is free — 5433 simply avoids the 5432 already in use.

Your connection URL:

```
postgresql://YOUR_USERNAME@localhost:5433/clouddesk_rag
```

### Option B — use the existing server

Nothing to create; it is already running on 5432. Your URL:

```
postgresql://YOUR_USERNAME@localhost:5432/clouddesk_rag
```

### Option C — managed Postgres

Create a project in the provider's console, enable the `vector` extension,
and copy the connection string they give you. It will look like:

```
postgresql://user:password@host.neon.tech/clouddesk_rag?sslmode=require
```

---

## Step 2 — tell the application where it is

```bash
cp .env.example .env
```

Edit `.env` and set the one line to the URL from Step 1 — replacing
`USER` with your actual username (`whoami` prints it):

```
DATABASE_URL=postgresql://kasiguruprasad@localhost:5433/clouddesk_rag
```

Check the app reads it:

```bash
python main.py db url
```

`.env` is gitignored, so the URL is never committed.

---

## Step 3 — create the database and its tables

```bash
python main.py db setup
```

This one command does four things:

1. connects to the server's `postgres` maintenance database
2. `CREATE DATABASE clouddesk_rag` if it is not there
3. `CREATE EXTENSION vector` — adds the `vector` column type
4. creates the `documents` and `chunks` tables plus their indexes

Expected output:

```
  url             : postgresql://you@localhost:5433/clouddesk_rag
  database created: True
  pgvector        : 0.8.6
  tables          : documents, chunks
  contents        : {'documents': 0, 'chunks': {}}
```

It is safe to re-run at any time.

---

## Step 4 — load your documents

Two routes. Pick one.

### From the exported files (recommended — you see what goes in)

```bash
python main.py import export
```

Reads all 6 articles plus their `.meta.json` sidecars, writes them to the
`documents` table, then chunks and embeds them. Takes a minute or two: it
runs the embedding model over ~300 chunks.

### From the SQL backup (faster — restores the previous state exactly)

```bash
python main.py db restore --file ~/clouddesk-db-backups/clouddesk_rag_20260908.sql
```

This restores documents **and** the pre-computed vectors, so no embedding
step is needed.

---

## Step 5 — verify

```bash
python main.py db status
```

```
  reachable : True
  exists    : True
  contents  : {'documents': 6, 'chunks': {'fixed': 82, 'markdown': 123, 'recursive': 103}}
```

Then a real question:

```bash
python main.py ask "How long is a CloudDesk password reset link valid?"
```

Expect `30 minutes [1]` with a citation to `HC-001-markdown-15`.

And the full evaluation, which should reproduce your recorded numbers:

```bash
python experiments/evaluate_golden_ab.py
# hit-rate@3  66.7% -> 75.0%  (+8.3pp)
# tally       R = 3, G = 0, Not-In-Corpus = 1
```

If those match, the database is correct.

---

## Step 6 — starting it again after a reboot (Option A only)

The cluster you created does not start itself. After a restart:

```bash
pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start
```

To make it automatic, create
`~/Library/LaunchAgents/com.clouddesk.postgres.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.clouddesk.postgres</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/opt/postgresql@17/bin/postgres</string>
    <string>-D</string><string>/Users/YOUR_USERNAME/pgdata/clouddesk</string>
    <string>-p</string><string>5433</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

Then:

```bash
launchctl load -w ~/Library/LaunchAgents/com.clouddesk.postgres.plist
```

Options B and C need nothing — Homebrew and your provider handle it.

---

## Day-to-day commands

| Command | What it does |
|---|---|
| `python main.py db status` | Reachable? Exists? What is in it? |
| `python main.py db url` | Which URL is in use |
| `python main.py db backup --file x.sql` | `pg_dump` to a file |
| `python main.py db restore --file x.sql` | Restore from a dump |
| `python main.py db reset` | Empty the tables (asks you to type the database name) |
| `python main.py import <path>` | Add documents from disk |
| `python main.py export --to export` | Write documents back out as files |
| `python main.py ingest` | Re-chunk and re-embed what is in the database |

All `db` commands accept `--url` to act on a different database without
changing `.env`.

---

## If something goes wrong

**`No PostgreSQL server answering at localhost:5433`**
The server is not running. Start it (Step 1), or check the port matches
`.env`.

**`Could not CREATE EXTENSION vector`**
`brew install pgvector` for a local server. On a managed provider, enable
the extension in their console — your role is not allowed to install it.

**`vector type not found in the database`**
`db setup` has not run against this database yet. Run Step 3.

**`db status` shows `exists: False`**
The server is up but the database is not created. Run Step 3.

**Wrong database — pointing at the old one**
`python main.py db url` prints exactly what the app is using. Environment
variables win over `.env`, so check for a stray `export DATABASE_URL=...`
in your shell.

---

## What you should end up with

- A PostgreSQL server you created and control
- One database, `clouddesk_rag`, holding two tables
- 6 documents, 308 chunks, each with a 384-dimension embedding
- A `.env` file with one line in it
- Roughly 10 MB on disk
