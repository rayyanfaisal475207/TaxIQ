# Supabase Setup Guide — TaxIQ Production Database

## Why Supabase?

Supabase provides a managed PostgreSQL instance with:
- Full Postgres 15+ (no limitations vs. standard PG)
- Built-in connection pooling (PgBouncer)
- Dashboard for visual table/data inspection
- Row Level Security (useful for multi-tenant Phase 2+)
- Free tier: 500 MB database, 2 projects

## Step-by-Step Setup

### 1. Create a Supabase Account
1. Go to [https://supabase.com](https://supabase.com)
2. Sign up with GitHub (recommended) or email
3. You'll land on the dashboard

### 2. Create a New Project
1. Click **"New Project"** on the dashboard
2. Fill in:
   - **Name:** `taxiq` (or `taxiq-prod`)
   - **Database Password:** Generate a strong password and **save it** — you'll need it for the connection string
   - **Region:** Choose the one closest to your users (for Pakistan: `ap-south-1` Mumbai, or `me-south-1` Bahrain)
   - **Plan:** Free tier is fine to start
3. Click **"Create new project"** — provisioning takes ~2 minutes

### 3. Get Your Connection String (Detailed UI Steps)
1. In your project dashboard, look at the left sidebar menu.
2. Click the **Settings** icon (it looks like a gear ⚙️ at the very bottom left).
3. In the Settings menu that opens, click on **Database**.
4. Scroll down to the **Connection string** section.
5. Click on the **URI** tab (next to Node.js, JDBC, etc).
6. Uncheck the "Use connection pooling" box. You want the **direct connection** that uses port `5432` (not port 6543).
7. You will see a string that looks like this:
   ```
   postgresql://postgres.[your-project-ref]:[YOUR-PASSWORD]@aws-0-[region].pooler.supabase.com:5432/postgres
   ```
8. **CRITICAL STEP for TaxIQ:** Change `postgresql://` to `postgresql+asyncpg://` at the beginning of the URL.
9. Copy the modified string, replace `[YOUR-PASSWORD]` with the database password you created in step 2 (remove the brackets), and paste it into your `.env` file:
   ```env
   DATABASE_URL=postgresql+asyncpg://postgres.[your-project-ref]:your_actual_password@aws-0-[region].pooler.supabase.com:5432/postgres
   ```

### 4. Connection Pooling (Important)
- Supabase uses **PgBouncer** on port `6543` (pooled) and direct connection on port `5432`
- For TaxIQ (which uses SQLAlchemy's own connection pool), use the **direct connection** on port `5432`:
  ```
  postgresql+asyncpg://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres
  ```
- SQLAlchemy handles its own pool (`pool_size=5, max_overflow=10` in `postgres.py`)

### 5. Run Migrations
Once your `.env` has the `DATABASE_URL` set:
```bash
# From the project root
alembic upgrade head
```
This creates all 7 TaxIQ tables in your Supabase Postgres instance.

### 6. Verify
1. Go to your Supabase dashboard → **Table Editor**
2. You should see: `users`, `user_context_profiles`, `sessions`, `messages`, `pipeline_runs`, `pipeline_steps`, `documents`
3. Start TaxIQ:
   ```bash
   uvicorn src.main:app --reload
   ```
4. Check the startup logs — you should see:
   ```
   TaxIQ starting up...
   PostgreSQL connected: postgresql+asyncpg://...
   ```

## Environment Variables Summary
```env
# For local development (Docker):
DATABASE_URL=postgresql+asyncpg://postgres:dev@localhost:5432/taxiq

# For production (Supabase):
DATABASE_URL=postgresql+asyncpg://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:5432/postgres

# Switch memory backend to Postgres:
MEMORY_BACKEND=postgres
```

## Free Tier Limits
| Resource | Free Tier Limit |
|---|---|
| Database size | 500 MB |
| API requests | Unlimited |
| Edge functions | 500K invocations/month |
| Storage | 1 GB |
| Projects | 2 active |

For TaxIQ's pipeline logging + conversation memory, 500 MB is sufficient for thousands of sessions. You'll only need to upgrade when you have many concurrent users.
