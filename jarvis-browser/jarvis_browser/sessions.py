"""Persistent browser sessions with a TTL and a wiped-on-close profile dir.

A session is the only place cookies are allowed to exist, and they live in a
private temp directory that is destroyed the moment the session ends (close,
TTL expiry, or service shutdown). Nothing is persisted outside it.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic


class SessionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class Session:
    session_id: str
    profile_dir: str
    javascript: bool
    created: float
    expires: float
    current_url: str = ""
    steps_run: int = 0
    history: list[str] = field(default_factory=list)

    def public(self, now: float) -> dict:
        return {
            "session_id": self.session_id,
            "javascript": self.javascript,
            "current_url": self.current_url,
            "steps_run": self.steps_run,
            "expires_in": max(0.0, round(self.expires - now, 1)),
        }


class SessionManager:
    """Create / look up / destroy sessions. Enforces TTL and a hard cap."""

    def __init__(
        self,
        backend,
        *,
        ttl: float = 600.0,
        max_sessions: int = 8,
        root: str | None = None,
        clock: Callable[[], float] = monotonic,
        wipe: Callable[[str], None] | None = None,
        mkdir: Callable[[], str] | None = None,
    ):
        self._backend = backend
        self._ttl = ttl
        self._max = max_sessions
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        if wipe is None:
            from .browser import wipe_dir

            wipe = wipe_dir
        self._wipe = wipe
        if mkdir is None:
            def mkdir() -> str:
                return tempfile.mkdtemp(prefix="jbsess-", dir=root or None)
        self._mkdir = mkdir

    # -- lifecycle ---------------------------------------------------------
    async def create(self, *, javascript: bool = True) -> Session:
        await self.reap()
        if len(self._sessions) >= self._max:
            raise SessionError(
                429, f"session limit reached ({self._max}); close one first"
            )
        now = self._clock()
        session = Session(
            session_id=uuid.uuid4().hex,
            profile_dir=self._mkdir(),
            javascript=javascript,
            created=now,
            expires=now + self._ttl,
        )
        self._sessions[session.session_id] = session
        try:
            await self._backend.open_session(
                session.session_id,
                javascript=javascript,
                profile_dir=session.profile_dir,
            )
        except Exception:
            self._sessions.pop(session.session_id, None)
            self._wipe(session.profile_dir)
            raise
        return session

    def get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionError(404, "unknown session")
        if self._clock() >= session.expires:
            raise SessionError(410, "session expired")
        return session

    def touch(self, session: Session) -> None:
        """Slide the TTL forward on use, never past a hard ceiling."""
        ceiling = session.created + self._ttl * 4
        session.expires = min(self._clock() + self._ttl, ceiling)

    async def close(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        try:
            await self._backend.close_session(session_id)
        finally:
            self._wipe(session.profile_dir)
        return True

    async def reap(self) -> int:
        """Close every expired session. Returns how many were closed."""
        now = self._clock()
        dead = [
            sid for sid, s in self._sessions.items() if now >= s.expires
        ]
        for sid in dead:
            await self.close(sid)
        return len(dead)

    async def close_all(self) -> None:
        for sid in list(self._sessions):
            await self.close(sid)

    def __len__(self) -> int:
        return len(self._sessions)

    def list_public(self) -> list[dict]:
        now = self._clock()
        return [s.public(now) for s in self._sessions.values()]
