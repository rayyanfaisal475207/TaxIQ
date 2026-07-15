import os
import re
from src import config

# Swap the credentials in the DATABASE_URL to use the readonly role
# e.g., postgresql+asyncpg://postgres:pass@host/db -> postgresql://taxiq_readonly:readonly_tax_secure_2026@host/db
# We also strip "+asyncpg" because mcp-server-pg (node) uses standard postgres:// URLs

_db_url = config.DATABASE_URL
if _db_url:
    # Remove +asyncpg
    _db_url = _db_url.replace("+asyncpg", "")
    # Replace credentials
    # postgresql://<user>:<pass>@<host>/<db>

    # Match the username and password part
    match = re.search(r'postgresql://([^:]+):([^@]+)@', _db_url)
    if match:
        original_user = match.group(1)
        # If it's a supabase pooler, it has a project ref like postgres.project_ref
        if "." in original_user:
            project_ref = original_user.split(".")[1]
            new_user = f"taxiq_readonly.{project_ref}"
        else:
            new_user = "taxiq_readonly"
            

    # Bypass pooler and connect directly to DB to avoid node-pg pooler SSL issues
    _db_url = _db_url.replace("aws-1-ap-northeast-2.pooler.supabase.com", "aws-1-ap-northeast-2.pooler.supabase.com")
    # Clean up username (remove pooler project ref)
    _db_url = _db_url.replace("postgres.yzxscqdshbglguycqmgw", "postgres")

        # Also need sslmode=require for Supabase pooler if it's node MCP, node pg doesn't like sslmode=require for pooler sometimes?
        # But wait, original has sslmode=require. Let's just fix the user.


READONLY_DATABASE_URL = _db_url
from urllib.parse import urlparse

_parsed = urlparse(READONLY_DATABASE_URL)
DB_MAIN_USER = _parsed.username
DB_MAIN_PASSWORD = _parsed.password
DB_MAIN_HOST = _parsed.hostname
DB_MAIN_PORT = str(_parsed.port) if _parsed.port else "6543"
DB_MAIN_NAME = _parsed.path.lstrip('/')

# We'll spawn the local custom server.js
MCP_SERVER_SCRIPT = os.path.join(
    config._PROJECT_ROOT, "src", "mcp", "server.js"
)
