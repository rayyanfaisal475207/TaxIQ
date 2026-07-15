# Backend Design

This document details the backend architecture of the RAG Chatbot, specifically focusing on the data access layer and the `DataGateway` abstraction.

## The DataGateway Abstraction

Early in development, a significant networking constraint was identified: the sandbox environment firewall strictly blocks outbound TCP connections on ports 5432 and 6543. Because the official Supabase Node.js SDK and `psycopg2` both rely on direct database connections via TCP/TLS, direct SQL queries against the Supabase Postgres instance were repeatedly failing with `WinError 64 (ECONNRESET)` and Timeout errors.

To bypass this network limitation without sacrificing functionality, the backend employs the **DataGateway abstraction**.

### Why the Gateway Exists
The DataGateway completely isolates the application's business logic from the underlying storage mechanism. Instead of relying on a standard SQL driver like `asyncpg` or `psycopg2`, the Gateway leverages the `supabase-py` SDK, which tunnels all database operations through the Supabase REST API (PostgREST) over standard HTTPS (Port 443).

By tunneling queries over HTTPS, the Gateway effectively circumvents the stringent TCP port blocking on the database level, ensuring reliable network communication from the restricted sandbox environment.

### Dependency Injection (`get_gateway()`)
The `DataGateway` instance is provided to FastAPI routes and pipeline components via dependency injection. 

```python
# Usage Example
from src.data_gateway import get_gateway

async def process_data():
    gateway = await get_gateway()
    user_data = await gateway.get_user(user_id)
```

**How it works**:
- A factory method `get_gateway()` dynamically instantiates and returns the configured backend implementation.
- The default implementation is `SupabaseRESTBackend`, which implements all essential database operations (`get_user`, `get_session_history`, `execute_query`, etc.) by wrapping PostgREST HTTP calls.
- The abstraction ensures that if network conditions change (e.g., direct TCP access is permitted), a direct PostgreSQL adapter could be seamlessly swapped in without requiring any changes to the core application routing, auth logic, or LLM pipeline code.
