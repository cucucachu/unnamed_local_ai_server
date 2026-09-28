import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "services" / "agent-server"))
from psycopg_pool import AsyncConnectionPool
from app.db import rls
_UNKNOWN = object()
class CachedRlsPool(AsyncConnectionPool):
    async def getconn(self, timeout=None):
        conn = await super().getconn(timeout)
        want = rls._user_id.get() or ""
        if getattr(conn, "_rls_user", _UNKNOWN) != want:
            conn._rls_user = _UNKNOWN
            await conn.execute("SELECT set_config('app.user_id', %s, false)", (want,))
            conn._rls_user = want
        return conn
