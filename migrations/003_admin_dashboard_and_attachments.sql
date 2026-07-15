-- ============================================================
-- TaxIQ — Migration 003
-- Admin analytics dashboard + per-conversation chat attachments
--
-- HOW TO APPLY
--   Supabase (any network):  paste this whole file into the
--     Supabase SQL editor and run it. It is idempotent.
--   Direct Postgres (office): psql "$DATABASE_URL" -f migrations/003_admin_dashboard_and_attachments.sql
--     or: python scripts/apply_migration.py migrations/003_admin_dashboard_and_attachments.sql
--
-- Until this runs, the new dashboard endpoints degrade gracefully:
-- they return empty datasets instead of 500s, and the UI shows a
-- "instrumentation not yet applied" notice.
-- ============================================================

-- ── 1. Error log ─────────────────────────────────────────────
-- Every ERROR/CRITICAL emitted by the backend lands here, so the
-- admin dashboard can show an error history and a trend over time
-- instead of the truth living only in a terminal that scrolled away.
CREATE TABLE IF NOT EXISTS error_logs (
    error_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    severity      text NOT NULL DEFAULT 'error',   -- warning | error | critical
    error_type    text,                            -- exception class, e.g. APIError
    module        text,                            -- logger name / source module
    message       text NOT NULL,
    stack_trace   text,
    run_id        uuid,                            -- pipeline run, when known
    session_id    uuid,
    user_id       uuid,
    context       jsonb
);

CREATE INDEX IF NOT EXISTS idx_error_logs_occurred_at ON error_logs (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_error_logs_severity    ON error_logs (severity);
CREATE INDEX IF NOT EXISTS idx_error_logs_module      ON error_logs (module);

-- ── 2. Ingestion jobs ────────────────────────────────────────
-- One row per admin document upload, so the KB page can show
-- processing / success / failed per document (with the reason).
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id        text,                            -- set once the document row exists
    filename      text NOT NULL,
    file_type     text,
    file_size_bytes bigint,
    status        text NOT NULL DEFAULT 'processing',  -- processing | success | failed
    chunks_added  integer DEFAULT 0,
    error_message text,
    uploaded_by   uuid,                            -- admin user id
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    duration_ms   integer
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_started_at ON ingestion_jobs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status     ON ingestion_jobs (status);

-- ── 3. Session attachments ───────────────────────────────────
-- Files a user attaches to ONE conversation from the chat composer.
--
-- These are deliberately NOT part of the knowledge base: their text
-- is extracted once and injected into that conversation's prompt.
-- They never enter `document_chunks`, are never embedded, and are
-- never retrievable by any other user or conversation. That keeps the
-- two systems separate by construction rather than by a filter that
-- someone could forget to apply.
CREATE TABLE IF NOT EXISTS session_attachments (
    attachment_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     uuid NOT NULL,
    user_id        uuid,
    filename       text NOT NULL,
    file_type      text,
    file_size_bytes bigint,
    extracted_text text,                           -- truncated to a token budget at read time
    char_count     integer,
    status         text NOT NULL DEFAULT 'ready',  -- ready | failed
    error_message  text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_attachments_session ON session_attachments (session_id);

-- ── 4. Chunks per document ───────────────────────────────────
-- Grouped by source_file, which is the identity a human recognises (and the
-- one retrieval cites). Grouping by documents.doc_id would be wrong for the
-- existing corpus: a bug in the chunker meant every chunk was written with its
-- own synthetic doc_id ("unknown_<chunk_id>"), so the documents table holds one
-- near-empty row per chunk. The chunker is fixed going forward; this view
-- reports the truth for both the old rows and the new ones.
CREATE OR REPLACE VIEW knowledge_base_documents AS
SELECT
    c.source_file                        AS filename,
    COUNT(*)                             AS chunk_count,
    MAX(d.doc_type)                      AS doc_type,
    bool_or(COALESCE(d.is_global, false)) AS is_global,
    MAX(d.ingested_at)                   AS ingested_at
FROM document_chunks c
LEFT JOIN documents d ON d.doc_id = c.doc_id
GROUP BY c.source_file;
