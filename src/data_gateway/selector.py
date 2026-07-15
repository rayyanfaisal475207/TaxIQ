import os
import logging
import asyncio

# Importing config guarantees .env is loaded before we read DB_ACCESS_MODE.
# Without this, the mode silently resolved to "auto" whenever this module
# was imported before src.config.
from src import config  # noqa: F401

logger = logging.getLogger(__name__)

from src.data_gateway.base import DataGateway

_gateway_instance = None

def _rest_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

async def _direct_connection_healthy(timeout: float = 6.0, attempts: int = 1) -> bool:
    """
    Probe the direct PostgreSQL connection.

    The first connection to the Supabase pooler includes an SSL handshake to a
    remote AWS host, which over IPv6 can take several seconds — the old 2.5s
    single-shot probe intermittently timed out and silently downgraded an
    explicitly-`direct` deployment to REST. Default is now a more forgiving 6s,
    and callers that have explicitly chosen direct pass attempts>1 to retry.
    """
    from src.database.postgres import engine
    if engine is None:
        return False
    from sqlalchemy import text
    for i in range(attempts):
        try:
            async with asyncio.timeout(timeout):
                async with engine.begin() as conn:
                    await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.debug("Direct DB probe attempt %d/%d failed: %s", i + 1, attempts, e)
            if i + 1 < attempts:
                await asyncio.sleep(1.0)
    return False

async def get_gateway() -> DataGateway:
    """
    Returns a configured DataGateway implementation based on the DB_ACCESS_MODE
    environment variable or an automatic startup probe.

    DB_ACCESS_MODE=direct prefers the direct PostgreSQL connection but falls
    back to REST when the DB is unreachable (e.g. IPv4-only networks), so the
    same .env works at the office and at home.
    """
    global _gateway_instance
    if _gateway_instance is not None:
        return _gateway_instance

    mode = os.getenv("DB_ACCESS_MODE", "auto").strip().lower()

    if mode == "rest":
        from src.data_gateway.rest_backend import RestGateway
        logger.info("DB_ACCESS_MODE=rest -> using REST backend")
        _gateway_instance = RestGateway()
        return _gateway_instance

    if mode == "direct":
        # Explicit choice — probe patiently (3 attempts, 8s each) before
        # downgrading, so a slow first handshake doesn't silently switch backends.
        if await _direct_connection_healthy(timeout=8.0, attempts=3):
            from src.data_gateway.direct_backend import DirectGateway
            logger.info("DB_ACCESS_MODE=direct -> using DIRECT backend")
            _gateway_instance = DirectGateway()
            return _gateway_instance
        if _rest_configured():
            from src.data_gateway.rest_backend import RestGateway
            logger.warning(
                "DB_ACCESS_MODE=direct but the direct DB is unreachable -> "
                "falling back to REST backend"
            )
            _gateway_instance = RestGateway()
            return _gateway_instance
        # No REST fallback available — return direct and let callers surface errors.
        from src.data_gateway.direct_backend import DirectGateway
        logger.error(
            "DB_ACCESS_MODE=direct, direct DB unreachable, and Supabase REST is "
            "not configured — database operations will fail until connectivity returns."
        )
        _gateway_instance = DirectGateway()
        return _gateway_instance

    # Auto mode: Probe direct connection
    if await _direct_connection_healthy():
        from src.data_gateway.direct_backend import DirectGateway
        logger.info("DB_ACCESS_MODE=auto -> using DIRECT backend")
        _gateway_instance = DirectGateway()
    else:
        from src.data_gateway.rest_backend import RestGateway
        logger.info("DB_ACCESS_MODE=auto -> direct unreachable, using REST backend")
        _gateway_instance = RestGateway()

    return _gateway_instance
