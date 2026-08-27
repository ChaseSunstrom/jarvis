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
    DEFAULT_MOMENTS,
    DEFAULT_SIZE,
    KINDS,
    MAX_MOMENTS,
    TYPES,
    DashboardStore,
    clean_dashboard,
    clean_widget,
    window_for,
)

SHIPPED = Path(__file__).resolve().parents[1] / "config/dashboards"

#: One value per field a kind may need, so a widget of any kind can be built
#: from the contract's `needs` list alone.
SAMPLE_FIELDS = {"type": "line", "source": "internal", "series": ["a"], "entity": "light.hall"}

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


# --- kinds (M63) -------------------------------------------------------------


def test_the_module_shows_the_kinds_the_contract_names():
    assert set(KINDS) == set(CONTRACT["kinds"]), (
        "the widget kinds the server accepts and the ones the contract describes "
        "have diverged; the console reads the contract"
    )


def test_a_widget_with_no_kind_is_a_graph_so_a_layout_saved_before_kinds_still_loads():
    """Every dashboard M62's console saved has no `kind` on any widget."""
    widget = clean_widget({"type": "line", "source": "internal", "series": ["host.load1"]}, 0)
    assert widget is not None
    assert widget["kind"] == "metric"
    assert widget["series"] == ["host.load1"]


def test_each_kind_cleans_with_the_fields_the_contract_says_it_needs():
    for kind, spec in CONTRACT["kinds"].items():
        raw = {"kind": kind, **{field: SAMPLE_FIELDS[field] for field in spec["needs"]}}
        widget = clean_widget(raw, 0)
        assert widget is not None, f"a {kind} widget with everything it needs was refused"
        for field in [*CONTRACT["widget"]["required"], *spec["needs"]]:
            assert field in widget, f"a cleaned {kind} widget has no {field!r}"


def test_a_kind_missing_what_it_needs_is_refused_not_drawn_blank():
    for kind, spec in CONTRACT["kinds"].items():
        for missing in spec["needs"]:
            raw = {
                "kind": kind,
                **{f: SAMPLE_FIELDS[f] for f in spec["needs"] if f != missing},
            }
            assert clean_widget(raw, 0) is None, f"a {kind} widget without {missing!r} was kept"


def test_a_kind_nobody_can_draw_is_refused():
    assert clean_widget({"kind": "weather"}, 0) is None


def test_an_entity_tile_needs_an_entity_id_the_state_machine_could_hold():
    """`hall lamp` is a name; a tile naming it would poll for a state that cannot exist."""
    assert clean_widget({"kind": "entity", "entity": "hall lamp"}, 0) is None
    assert clean_widget({"kind": "entity", "entity": "light."}, 0) is None
    tile = clean_widget({"kind": "entity", "entity": "light.hall_lamp"}, 0)
    assert tile is not None and tile["entity"] == "light.hall_lamp"


def test_a_camera_widget_may_leave_the_camera_unnamed():
    """The shipped House cannot know what an installation calls its front door."""
    widget = clean_widget({"kind": "camera"}, 0)
    assert widget is not None and widget["camera"] == ""
    named = clean_widget({"kind": "camera", "camera": "Front Door"}, 0)
    assert named is not None and named["camera"] == "Front Door"


def test_a_moments_widget_is_a_glance_not_the_inbox():
    assert clean_widget({"kind": "moments"}, 0)["limit"] == DEFAULT_MOMENTS
    assert clean_widget({"kind": "moments", "limit": 500}, 0)["limit"] == MAX_MOMENTS
    assert clean_widget({"kind": "moments", "limit": 0}, 0)["limit"] == 1


def test_each_kind_has_its_own_footprint_when_the_client_sent_none():
    for kind, (width, height) in DEFAULT_SIZE.items():
        raw = {"kind": kind, **{f: SAMPLE_FIELDS[f] for f in CONTRACT["kinds"][kind]["needs"]}}
        widget = clean_widget(raw, 0)
        assert (widget["w"], widget["h"]) == (width, height), kind


def test_a_metric_widget_does_not_carry_another_kind_s_fields():
    """An entity on a graph, or a series on a tile, is a field nobody reads."""
    graph = clean_widget({"type": "line", "series": ["a"], "entity": "light.x"}, 0)
    assert "entity" not in graph
    tile = clean_widget({"kind": "entity", "entity": "light.x", "series": ["a"]}, 0)
    assert "series" not in tile and "type" not in tile


async def test_a_layout_saved_before_kinds_survives_a_reload(tmp_path):
    """The JSON M62 wrote, byte for byte: no `kind` anywhere."""
    backing = MemoryStore()
    backing.data = {
        "dashboards": [
            {
                "id": "old",
                "title": "Old",
                "owner": "token-a",
                "range": "6h",
                "updated": 1.0,
                "widgets": [
                    {
                        "id": "w1",
                        "type": "stat",
                        "source": "internal",
                        "series": ["host.disk_free"],
                        "aggregate": "last",
                        "x": 0,
                        "y": 0,
                        "w": 3,
                        "h": 2,
                    }
                ],
            }
        ]
    }
    store = DashboardStore(FakeJarvis(tmp_path), store=backing)
    await store.async_load()
    [board] = store.visible_to("token-a")
    assert board["widgets"][0]["kind"] == "metric"
    assert board["widgets"][0]["series"] == ["host.disk_free"]


def test_the_shipped_house_shows_the_house_and_names_no_device_nobody_owns():
    board = clean_dashboard(yaml.safe_load((SHIPPED / "house.yaml").read_text()))
    assert board is not None, "the shipped House is not a dashboard"
    kinds = {w["kind"] for w in board["widgets"]}
    assert kinds >= {"entity", "readings", "camera", "sky", "moments"}, kinds
    # The one entity tile is the sun: every install with `sun:` has it, and a
    # default configuration that invents a light nobody owns is worse than
    # one that controls nothing.
    tiles = [w["entity"] for w in board["widgets"] if w["kind"] == "entity"]
    assert tiles == ["sun.sun"], tiles
    cameras = [w["camera"] for w in board["widgets"] if w["kind"] == "camera"]
    assert cameras == [""], "the House cannot know what an install calls its camera"


def test_a_fresh_install_opens_on_the_house_not_the_machine(tmp_path):
    store = DashboardStore(FakeJarvis(tmp_path), store=MemoryStore())
    store.load_shipped(SHIPPED)
    assert store.shipped[0]["id"] == "house", [b["id"] for b in store.shipped]
    assert "homelab" in [b["id"] for b in store.shipped]
    assert store.visible_to("anybody")[0]["id"] == "house"
