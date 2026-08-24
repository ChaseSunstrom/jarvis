"""Dashboards: what a saved layout is, and whose it is.

The contract is `tests/contracts/dashboard_layout.json`, which this file and the
console's `src/lib/dashboards/layout.test.ts` both read. A layout is the one
piece of state a user builds by hand, so the failure worth preventing is one
side writing something the other refuses.

Ownership is the other half. There are no user accounts here — a token is the
identity — so "per user" means "per token id", and the rule that matters is
that a token can never read or overwrite somebody else's board.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jarvis.integrations.dashboards import (
    COLUMNS,
    TYPES,
    DashboardStore,
    clean_dashboard,
    clean_widget,
    window_for,
)

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/contracts/dashboard_layout.json").read_text()
)


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict = {}

    async def load(self):
        return self.data

    async def save(self, data):
        self.data = data


class FakeJarvis:
    def __init__(self, tmp_path) -> None:
        self.config_dir = tmp_path
        self.data: dict = {}


@pytest.fixture
def store(tmp_path) -> DashboardStore:
    return DashboardStore(FakeJarvis(tmp_path), store=MemoryStore())


# --- the contract ------------------------------------------------------------


def test_the_module_draws_the_types_the_contract_names():
    assert set(TYPES) == set(CONTRACT["types"]), (
        "the chart types the server accepts and the ones the contract describes "
        "have diverged; the console reads the contract"
    )


def test_the_grid_is_the_one_the_contract_describes():
    assert COLUMNS == CONTRACT["columns"]


def test_a_widget_carries_every_field_the_contract_requires():
    widget = clean_widget({"type": "line", "source": "internal", "series": ["a"]}, 0)
    for field in CONTRACT["widget"]["required"]:
        assert field in widget, f"a cleaned widget has no {field!r}"


def test_a_dashboard_carries_every_field_the_contract_requires():
    board = clean_dashboard({"id": "b", "title": "B", "widgets": []})
    for field in CONTRACT["dashboard"]["required"]:
        assert field in board


# --- what a layout may be ----------------------------------------------------


def test_a_widget_with_a_type_nobody_can_draw_is_refused_not_saved():
    """A blank rectangle somebody has to delete is worse than a refusal."""
    assert clean_widget({"type": "pie", "series": ["a"]}, 0) is None


def test_a_widget_with_no_series_is_refused():
    assert clean_widget({"type": "line", "series": []}, 0) is None


def test_coordinates_are_clamped_to_the_grid():
    widget = clean_widget({"type": "line", "series": ["a"], "x": 99, "w": 40, "h": -3}, 0)
    assert widget["x"] == COLUMNS - 1
    assert widget["w"] == COLUMNS
    assert widget["h"] >= 1


def test_a_dashboard_needs_a_title_and_an_id():
    assert clean_dashboard({"widgets": []}) is None
    assert clean_dashboard({"title": "Homelab", "widgets": []})["id"] == "homelab"


def test_an_unknown_range_falls_back_rather_than_being_stored():
    assert clean_dashboard({"title": "B", "range": "forever"})["range"] == "6h"


# --- whose it is -------------------------------------------------------------


async def test_a_board_belongs_to_the_token_that_saved_it(store):
    await store.async_put({"id": "mine", "title": "Mine", "widgets": []}, owner="token-a")
    assert [b["id"] for b in store.visible_to("token-a")] == ["mine"]
    assert store.visible_to("token-b") == []


async def test_one_token_cannot_overwrite_another_s_board(store):
    await store.async_put({"id": "same", "title": "A's", "widgets": []}, owner="token-a")
    await store.async_put({"id": "same", "title": "B's", "widgets": []}, owner="token-b")
    titles = {b["owner"]: b["title"] for b in store.saved}
    assert titles == {"token-a": "A's", "token-b": "B's"}


async def test_one_token_cannot_delete_another_s_board(store):
    await store.async_put({"id": "same", "title": "A's", "widgets": []}, owner="token-a")
    assert await store.async_delete("same", "token-b") is False
    assert await store.async_delete("same", "token-a") is True


async def test_a_board_with_no_owner_is_shared(store):
    await store.async_put({"id": "wall", "title": "Wall panel", "widgets": []}, owner="")
    assert [b["id"] for b in store.visible_to("anybody")] == ["wall"]


async def test_saving_the_same_id_replaces_rather_than_duplicates(store):
    await store.async_put({"id": "b", "title": "One", "widgets": []}, owner="t")
    await store.async_put({"id": "b", "title": "Two", "widgets": []}, owner="t")
    assert len(store.saved) == 1
    assert store.saved[0]["title"] == "Two"


async def test_a_layout_survives_a_restart(store, tmp_path):
    backing = MemoryStore()
    first = DashboardStore(FakeJarvis(tmp_path), store=backing)
    await first.async_put(
        {
            "id": "homelab",
            "title": "Homelab",
            "widgets": [{"type": "line", "source": "internal", "series": ["host.load1"]}],
        },
        owner="token-a",
    )
    second = DashboardStore(FakeJarvis(tmp_path), store=backing)
    await second.async_load()
    assert [b["id"] for b in second.visible_to("token-a")] == ["homelab"]
    assert second.saved[0]["widgets"][0]["series"] == ["host.load1"]


# --- what ships --------------------------------------------------------------


def test_the_shipped_example_is_a_dashboard_this_code_accepts():
    """The example is the answer to "what would I put on one" — it must load."""
    path = Path(__file__).resolve().parents[1] / "config/dashboards/homelab.yaml"
    board = clean_dashboard(yaml.safe_load(path.read_text()))
    assert board is not None, "the shipped dashboard is not a dashboard"
    assert len(board["widgets"]) >= 4
    # Every chart type the console can draw appears somewhere, so the example is
    # also the thing that proves each one renders.
    assert {w["type"] for w in board["widgets"]} >= {"line", "area", "stat", "bar"}


def test_shipped_boards_are_owned_by_nobody(tmp_path):
    store = DashboardStore(FakeJarvis(tmp_path), store=MemoryStore())
    store.load_shipped(Path(__file__).resolve().parents[1] / "config/dashboards")
    assert store.shipped and all(not b["owner"] for b in store.shipped)
    assert all(b.get("shipped") for b in store.shipped)


# --- windows -----------------------------------------------------------------


def test_a_named_range_is_the_window_it_says():
    window = window_for({"range": "6h"})
    assert 21590 <= window.span <= 21610


def test_an_upside_down_window_is_refused_rather_than_drawn_backwards():
    window = window_for({"start": 100, "end": 50})
    assert window.span > 0
