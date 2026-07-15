# Running TaxIQ from scratch

Everything needed to bring the whole stack up on a fresh machine, and to stop it
cleanly. Written against a live, tested run (backend on `:8000`, chat app on
`:5173`, admin app on `:5174`, Supabase Postgres over IPv6 in direct mode).

- [1. Prerequisites](#1-prerequisites)
- [2. Environment variables](#2-environment-variables)
- [3. Install](#3-install)
- [4. First-run setup (migrations)](#4-first-run-setup-migrations)
- [5. Start everything](#5-start-everything)
- [6. Smoke test](#6-smoke-test)
- [7. Stop everything](#7-stop-everything)
- [8. Troubleshooting](#8-troubleshooting)

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.13 works; 3.9 is too old for some type syntax used |
| Node.js | 20+ | with `npm` |
| PostgreSQL | 15+ with `pgvector` | Supabase provides this; or `docker compose up` for local |
| Git | any | |

A local Postgres is **optional** — the default setup uses a hosted Supabase
database. You only need Docker if you want to run Postgres locally
(`docker-compose.yml` is included).

**API keys you must have** (all free tiers work):

- **Groq** — primary chat model. https://console.groq.com
- **Google Gemini** — vision/OCR + fallback model. https://aistudio.google.com/app/apikey
- **Tavily** — web-search route. https://tavily.com (optional; WEB route degrades without it)

---

## 2. Environment variables

Copy the template and fill it in:

```bash
cp .env.example .env
```

| Variable | Required | Where to get it / what to set |
|---|---|---|
| `DATABASE_URL` | **yes** | Supabase → Project Settings → Database → Connection string (URI). Must start `postgresql+asyncpg://`. Use the **pooler** host for IPv4 networks. |
| `SUPABASE_URL` | yes | Supabase → Project Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Supabase → Project Settings → API → `service_role` key (secret — never ship to the browser) |
| `DB_ACCESS_MODE` | yes | `direct` (async Postgres, needs IPv6 or a reachable pooler), `rest` (Supabase REST API), or `auto` (probe then fall back). See note below. |
| `GROQ_API_KEY` | yes | console.groq.com. Multiple keys: `GROQ_API_KEY_1`, `_2`, … for auto-rotation on rate-limit. |
| `GEMINI_API_KEY` | yes | aistudio.google.com |
| `TAVILY_API_KEY` | no | tavily.com — enables the WEB route |
| `LLM_PROVIDER` | yes | `groq` (recommended) or `gemini` |
| `GROQ_MODEL` | yes | `llama-3.3-70b-versatile` |
| `GEMINI_MODEL` | yes | `gemini-2.5-flash` |
| `EMBEDDING_PROVIDER` | yes | `local` (384-dim, no API cost — matches the current DB) or `gemini` |
| `JWT_SECRET_KEY` | yes | any long random string — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ENVIRONMENT` | yes | `development` locally (sets non-secure cookies so `http://localhost` works) |
| `LOCAL_LLM_URL` | no | **Leave empty.** Only set to an OpenAI-compatible URL if you run a local model. A non-empty stale value adds a timeout to every LLM call. |
| `MAX_RETRIES` | no | retrieval retry budget (default 1–2) |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` | no | 10 / 5 |

> **Which `DB_ACCESS_MODE`?** `direct` is faster and is what the app uses when
> the Postgres wire protocol is reachable (office LAN, or any IPv6 network — the
> Supabase pooler is IPv6). On an IPv4-only network where direct is blocked, use
> `rest`, or `auto` to probe-and-fall-back. In `direct` mode the app still falls
> back to REST automatically if the probe fails, so `direct` is a safe default
> when Supabase REST keys are also set.

> **`EMBEDDING_PROVIDER` must match the corpus.** The existing 88k chunks were
> embedded with the 384-dim local model. Switching to `gemini` without
> re-ingesting everything will silently return garbage retrieval (mismatched
> vector spaces). Keep it `local` unless you re-embed the whole corpus.

---

## 3. Install

**Backend** (from the repo root):

```bash
python -m venv .venv
source .venv/bin/activate          # Windows Git Bash: source .venv/Scripts/activate
                                    # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Frontends** (two separate apps):

```bash
cd frontend && npm install && cd ..
cd admin-frontend && npm install && cd ..
```

---

## 4. First-run setup (migrations)

The schema lives in two places and both must be applied once.

```bash
# 1. The SQLAlchemy migration chain
alembic upgrade head

# 2. The dashboard + attachments tables (not in the Alembic chain)
python scripts/apply_migration.py migrations/003_admin_dashboard_and_attachments.sql
```

`migration 003` creates `error_logs`, `ingestion_jobs`, `session_attachments`,
and the `knowledge_base_documents` view. **Until it runs, chat attachments are
disabled and the admin dashboard shows an "instrumentation not applied" banner
instead of error/ingestion data.**

If `apply_migration.py` fails with a connection error (IPv4-only network blocking
Postgres), open the Supabase **SQL editor**, paste the contents of
`migrations/003_admin_dashboard_and_attachments.sql`, and run it there — the REST
API cannot execute DDL.

**Vector index (one-time, important for latency).** Retrieval needs an HNSW index
on `document_chunks.embedding`. It already exists on the current database. On a
fresh database, create it once:

```sql
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
  ON document_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS document_chunks_fts_vector_idx
  ON document_chunks USING gin (fts_vector);
```

**Create an admin user.** The admin panel requires `is_admin = true`:

```bash
python scripts/create_admin.py
# Creates admin@taxiq.com with a default password (TaxIQAdmin2026!).
# CHANGE THIS PASSWORD before any real deployment — edit the script, or
# reset it in the Supabase table editor after creation.
```

Or promote an existing account: Supabase table editor → `users` → your row →
set `is_admin = true`.

**Populate the knowledge base** (optional — needed for the RAG route to answer):
upload documents from the admin app (**Knowledge Base → drop files**), or bulk-load
with the scripts in `scripts/` (e.g. `python scripts/ingest_fbr_docs.py`).

---

## 5. Start everything

There is **no worker, queue, or separate vector store** to run — retrieval and
vectors live inside Postgres, and background work (memory updates, error logging)
runs on the backend's own event loop. So it's three processes: the API and the
two frontends. The database is hosted (Supabase), so nothing to start there.

Open three terminals (each with the venv active for the backend one):

```bash
# Terminal 1 — API  (http://localhost:8000)
source .venv/bin/activate
uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — Chat app  (http://localhost:5173)
cd frontend && npm run dev

# Terminal 3 — Admin app  (http://localhost:5174)
cd admin-frontend && npm run dev
```

Start the **API first** — both frontends proxy `/api` to `http://127.0.0.1:8000`
and will error until it is up. Confirm it's healthy:

```bash
curl http://localhost:8000/health
# {"status":"ok", ... "documents_in_store": 88107}
```

The startup log should print the selected backend:

```
src.data_gateway.selector: DB_ACCESS_MODE=direct -> using DIRECT backend
```

If you see `... -> falling back to REST backend` instead, direct Postgres is
unreachable on this network (see Troubleshooting).

**URLs**

| App | URL | Login |
|---|---|---|
| Chat | http://localhost:5173 | register a new account in the UI |
| Admin | http://localhost:5174 | an account with `is_admin = true` |

---

## 6. Smoke test

After the three processes are up:

1. Open http://localhost:5173, register, and send *"What is the penalty under
   Section 182 for late filing?"* — you should see the live status states, then a
   grounded answer citing the section. First answer is ~15–20s (cold model +
   embeddings), follow-ups are faster.
2. Attach a small `.txt` from the composer and ask about its contents — the answer
   should quote the file.
3. Open http://localhost:5174, log in as admin — the **Dashboard** should show a
   non-zero chunk count and real request/latency charts; **Knowledge Base** should
   let you drop a file and watch it go processing → success.

---

## 7. Stop everything

In each terminal press **Ctrl-C**. That's the clean shutdown — the backend runs
its lifespan shutdown, and the Vite dev servers stop immediately.

If a process was backgrounded or a port is stuck:

```bash
# macOS / Linux
lsof -ti:8000 | xargs kill      # repeat for 5173, 5174

# Windows (PowerShell)
Get-NetTCPConnection -LocalPort 8000 | Select-Object -Expand OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }

# Windows (Git Bash)
netstat -ano | grep ":8000 " | awk '{print $5}' | head -1 | xargs -I{} taskkill //F //PID {}
```

Nothing else needs stopping — the database is hosted, and there are no workers or
local services.

---

## 8. Troubleshooting

Issues actually hit while bringing this up and testing it.

**`... -> falling back to REST backend` when you wanted direct.**
The Supabase pooler's first SSL handshake can take several seconds; a slow
network makes the probe time out. The selector now retries (3× / 8s) before
falling back, but on a genuinely IPv4-only network direct is blocked entirely —
set `DB_ACCESS_MODE=rest`. To confirm reachability:
`python -c "import asyncpg,os,asyncio; from dotenv import load_dotenv; load_dotenv(); asyncio.run(asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')))"`
— a hang or `ConnectionResetError` means the wire protocol is blocked.

**`apply_migration.py` fails: "cannot insert multiple commands into a prepared statement".**
Already fixed — the script uses a raw asyncpg connection. If you see it, you're on
an old copy of the script; pull the latest.

**`apply_migration.py` fails with a connection/network error.**
Your network blocks the Postgres wire protocol. Paste the `.sql` file into the
Supabase SQL editor instead — the REST API cannot run DDL.

**Admin dashboard shows "instrumentation not applied" / attachments say "not enabled".**
Migration 003 hasn't been applied to this database. Run step 4.

**Chat answers are slow (20s+) or the first one hangs for a long time.**
- The **first** request pays a cold cost: the local embedding model loads (~3s)
  and the LLM/DB connections warm up. This is one-time per process.
- If **every** request is slow, check `LOCAL_LLM_URL` is empty in `.env` — a stale
  value makes every one of the 5–6 LLM calls per query wait on a dead endpoint.
- If retrieval specifically is slow (visible in the admin per-step chart), the
  HNSW vector index is missing — create it (step 4).

**Retrieval returns nothing / RAG always says "no information".**
Either the knowledge base is empty (upload documents), or `EMBEDDING_PROVIDER`
doesn't match how the corpus was embedded (must be `local` for the current DB).

**Login works but every API call 401s afterward.**
Cookies aren't being set/sent. Set `ENVIRONMENT=development` in `.env` (secure
cookies require HTTPS and won't stick on `http://localhost`). Make sure you're
using `localhost`, not `127.0.0.1`, consistently across the frontend and API.

**Admin login rejected for a valid account.**
The account needs `is_admin = true`. The admin app verifies the flag via
`/api/auth/me` after login and refuses non-admins. Set it in the Supabase table
editor.

**`ModuleNotFoundError` / import errors on backend start.**
The venv isn't active, or `pip install -r requirements.txt` didn't finish. Re-activate
and reinstall.

**Windows console crashes on a non-ASCII error (`UnicodeEncodeError: charmap`).**
Some scripts print characters cp1252 can't encode. Run with `PYTHONIOENCODING=utf-8`
(the migration script already forces this).
