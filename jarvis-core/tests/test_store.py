"""The store's atomic save, under the one condition the lock cannot cover.

`Store.save` serialises writers with an asyncio.Lock — but a task cancelled
while it awaits the writing thread releases the lock with the thread still
inside `_save_sync`, and the next save shares the temp file. On 27 Aug 2026 a
timer re-armed at the instant it finished did exactly that, and the second
save died with FileNotFoundError at the chmod (CI, Python 3.12). Each write
now has a temp file of its own, so two threads writing at once both finish
and the last rename wins.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.store import Store  # noqa: E402


def test_two_threads_writing_the_same_store_both_finish(tmp_path):
    store = Store(tmp_path, "race", 1)
    errors: list[BaseException] = []

    def write(n: int) -> None:
        try:
            for i in range(60):
                store._save_sync({"writer": n, "i": i})
        except BaseException as err:  # noqa: BLE001 - the assertion is that there are none
            errors.append(err)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:2]
    payload = json.loads(store.path.read_text())
    assert payload["data"]["i"] == 59
    # Nothing left behind: every temp file was renamed or removed.
    assert [p.name for p in tmp_path.rglob("*.tmp")] == []
    assert (store.path.stat().st_mode & 0o777) == 0o600


@pytest.mark.asyncio
async def test_a_save_cancelled_mid_thread_does_not_break_the_next_one(tmp_path):
    store = Store(tmp_path, "cancelled", 1)
    first = asyncio.ensure_future(store.save({"n": 1}))
    await asyncio.sleep(0)
    first.cancel()
    try:
        await first
    except asyncio.CancelledError:
        pass
    await store.save({"n": 2})
    assert json.loads(store.path.read_text())["data"]["n"] == 2
