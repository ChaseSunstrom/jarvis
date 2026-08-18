"""The judgements a research run makes, tested without a network.

What separates research from a search box is three decisions, and each of them
fails in a way that still produces a document:

  * one query instead of several — a report on one angle of the question;
  * twelve pages from one site — that site's account of itself, with citations;
  * a citation to a page nobody read — invented corroboration.

Every test here is aimed at one of those.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.integrations.research.plan import (  # noqa: E402
    MAX_QUERIES,
    Note,
    Source,
    collect_sources,
    domain_of,
    format_report,
    is_empty_note,
    normalise_url,
    one_line_result,
    parse_queries,
    rank_sources,
    read_steps,
    search_steps,
    synthesis_prompt,
)


def src(url: str, **kw) -> Source:
    return Source(url=url, **kw)


def note(url: str, text: str = "a fact", ok: bool = True, error: str = "") -> Note:
    return Note(source=src(url, title=f"title of {url}"), text=text, ok=ok, error=error)


# --- reading the planner's answer ---------------------------------------------

def test_a_clean_json_array_is_read_as_written():
    got = parse_queries('["heat pump cop", "heat pump cost uk"]', question="q")
    assert got == ["heat pump cop", "heat pump cost uk"]


def test_json_buried_in_the_prose_the_model_was_told_not_to_write():
    raw = 'Sure! Here are some searches:\n["a", "b"]\nHope that helps.'
    assert parse_queries(raw, question="q") == ["a", "b"]


def test_a_numbered_list_is_read_too():
    # Asked for JSON, given a list. Constantly.
    raw = "1. first search\n2. second search\n3. third search"
    assert parse_queries(raw, question="q") == ["first search", "second search", "third search"]


def test_a_bulleted_list_is_read_too():
    assert parse_queries("- alpha\n* beta\n• gamma", question="q") == ["alpha", "beta", "gamma"]


def test_a_planner_that_returned_nothing_usable_falls_back_to_the_question():
    # One plain search is a far better outcome than a run that produces nothing
    # because the planning step was chatty.
    assert parse_queries("", question="how loud is a heat pump") == [
        "how loud is a heat pump"
    ]
    assert parse_queries("ok!", question="how loud is a heat pump") == [
        "how loud is a heat pump"
    ]


def test_the_same_query_twice_is_one_query():
    assert parse_queries('["a", "A", "a"]', question="q") == ["a"]


def test_the_number_of_queries_is_capped():
    many = json.dumps([f"query {i}" for i in range(50)])
    assert len(parse_queries(many, question="q")) == MAX_QUERIES
    assert len(parse_queries(many, question="q", limit=2)) == 2


def test_a_heading_line_is_not_a_query():
    raw = "Here are the searches:\n- real one\n- another real one"
    assert parse_queries(raw, question="q") == ["real one", "another real one"]


# --- deduplicating what came back ---------------------------------------------

def test_the_same_page_from_three_queries_is_one_source():
    # Three slots of the read budget, and three citations that look like
    # corroboration, for one page.
    per_query = [
        ("a", [{"url": "https://example.com/post#intro", "title": "Post"}]),
        ("b", [{"url": "https://www.example.com/post/", "title": "Post"}]),
        ("c", [{"url": "https://example.com/post?utm_source=news", "title": "Post"}]),
    ]
    sources = collect_sources(per_query)
    assert len(sources) == 1
    assert sources[0].queries == ["a", "b", "c"]


def test_normalising_leaves_a_meaningful_query_string_alone():
    # `?id=4` is the page. Only campaign parameters go.
    assert normalise_url("https://example.com/view?id=4&utm_source=x") == (
        "https://example.com/view?id=4"
    )


def test_normalising_survives_something_that_is_not_a_url():
    assert normalise_url("not a url") == "not a url"
    assert normalise_url("") == ""


def test_the_domain_ignores_the_www_and_the_port():
    assert domain_of("https://www.example.com:8443/x") == "example.com"
    assert domain_of("nonsense") == ""


def test_a_source_keeps_the_best_rank_it_reached_anywhere():
    per_query = [
        ("a", [{"url": "https://x.test/1"}, {"url": "https://y.test/1"}]),
        ("b", [{"url": "https://y.test/1"}]),
    ]
    by_url = {s.url: s for s in collect_sources(per_query)}
    assert by_url["https://y.test/1"].best_rank == 0


def test_a_result_with_no_url_is_skipped_rather_than_becoming_a_blank_source():
    sources = collect_sources([("a", [{"title": "no url"}, None, {"url": ""}])])
    assert sources == []


def test_a_title_missing_from_the_first_hit_is_taken_from_a_later_one():
    per_query = [
        ("a", [{"url": "https://x.test/1"}]),
        ("b", [{"url": "https://x.test/1", "title": "The real title", "snippet": "s"}]),
    ]
    source = collect_sources(per_query)[0]
    assert source.title == "The real title"
    assert source.snippet == "s"


# --- choosing what to read ------------------------------------------------------

def test_a_page_several_queries_found_outranks_one_that_led_a_single_query():
    corroborated = src("https://a.test/1", title="a")
    corroborated.queries = ["one", "two"]
    corroborated.best_rank = 4
    top_of_one = src("https://b.test/1", title="b")
    top_of_one.queries = ["one"]
    top_of_one.best_rank = 0
    assert [s.url for s in rank_sources([top_of_one, corroborated])] == [
        "https://a.test/1",
        "https://b.test/1",
    ]


def test_no_single_site_can_own_the_read_list():
    """The failure this prevents reads as thorough and is the opposite.

    One vendor's documentation can hold the top twelve results for a technical
    question. Twelve pages of it is that vendor's own account of itself, dressed
    up with citations.
    """
    hogs = []
    for i in range(10):
        s = src(f"https://vendor.test/doc{i}")
        s.queries = ["q"]
        s.best_rank = i
        hogs.append(s)
    others = []
    for i in range(3):
        s = src(f"https://other{i}.test/page")
        s.queries = ["q"]
        s.best_rank = 20 + i
        others.append(s)

    chosen = rank_sources(hogs + others, limit=6, per_domain=2)
    assert sum(1 for s in chosen if s.domain == "vendor.test") == 2
    assert len({s.domain for s in chosen}) == 4


def test_a_site_that_really_does_have_two_relevant_pages_keeps_both():
    a, b = src("https://x.test/1"), src("https://x.test/2")
    for s in (a, b):
        s.queries = ["q"]
    assert len(rank_sources([a, b], limit=6, per_domain=2)) == 2


def test_the_read_list_is_capped_even_when_every_source_is_a_different_site():
    sources = []
    for i in range(30):
        s = src(f"https://s{i}.test/p")
        s.queries = ["q"]
        sources.append(s)
    assert len(rank_sources(sources, limit=5)) == 5


def test_ranking_is_stable_for_sources_that_tie():
    a, b = src("https://b.test/1"), src("https://a.test/1")
    for s in (a, b):
        s.queries = ["q"]
        s.best_rank = 1
    assert [s.url for s in rank_sources([a, b])] == [
        "https://a.test/1",
        "https://b.test/1",
    ]


# --- the steps a bar is a fraction of -------------------------------------------

def test_steps_name_what_is_happening():
    assert search_steps(["cop of a heat pump", "heat pump noise"]) == [
        "search: cop of a heat pump",
        "search: heat pump noise",
    ]


def test_a_read_step_names_the_site_rather_than_a_number():
    # "read nginx.org" tells you what is happening; "step 7 of 11" does not,
    # and that difference is most of what a progress bar is for.
    steps = read_steps([src("https://www.nginx.org/docs"), src("https://caddyserver.com/x")])
    assert steps == ["read nginx.org", "read caddyserver.com", "write it up"]


def test_a_source_with_no_parseable_host_still_gets_a_readable_step():
    assert read_steps([src("not-a-url")]) == ["read not-a-url", "write it up"]


def test_a_run_with_nothing_to_read_still_has_a_write_up_step():
    assert read_steps([]) == ["write it up"]


# --- the report -----------------------------------------------------------------

def test_a_citation_to_a_page_nobody_read_is_struck_out():
    """Invented corroboration, and invisible unless somebody counts.

    Two sources were read; `[9]` cannot be one of them. Rewriting it to `[?]`
    rather than deleting it means the reader can see that it happened.
    """
    report = format_report(
        "q",
        "Something true [1]. Something else [9].",
        [note("https://a.test/1"), note("https://b.test/1")],
        queries=["q"],
        found=2,
    )
    assert "[1]" in report
    assert "[9]" not in report
    assert "[?]" in report


def test_a_valid_citation_survives_untouched():
    report = format_report(
        "q", "Fact [2].", [note("https://a.test/1"), note("https://b.test/1")],
        queries=["q"], found=2,
    )
    assert "Fact [2]." in report


def test_every_source_that_was_read_is_listed_and_numbered_to_match():
    report = format_report(
        "q", "x [1] y [2].",
        [note("https://a.test/1"), note("https://b.test/2")],
        queries=["q"], found=2,
    )
    assert "1. [title of https://a.test/1](https://a.test/1)" in report
    assert "2. [title of https://b.test/2](https://b.test/2)" in report


def test_pages_that_could_not_be_read_are_named_rather_than_dropped():
    # Dropping them makes a run that read two of twelve sources look like one
    # that read two.
    report = format_report(
        "q", "thin answer [1].",
        [
            note("https://a.test/1"),
            note("https://dead.test/1", ok=False, error="404"),
            note("https://empty.test/1", text=""),
        ],
        queries=["q"], found=3,
    )
    assert "https://dead.test/1 — 404" in report
    assert "https://empty.test/1 — read, nothing relevant" in report


def test_the_report_says_how_much_of_what_it_found_it_actually_read():
    report = format_report(
        "q", "answer [1].",
        [note("https://a.test/1")] + [note(f"https://d{i}.test/1", ok=False) for i in range(3)],
        queries=["one", "two"], found=11,
    )
    assert "Read 1 of 11 pages found across 2 searches" in report


def test_a_run_that_learned_nothing_says_so_instead_of_writing_an_empty_page():
    report = format_report("q", "", [], queries=["q"], found=0)
    assert "did not answer" in report
    assert "Read 0 of 0 pages" in report


def test_the_numbers_agree_in_the_singular():
    report = format_report("q", "a [1].", [note("https://a.test/1")], queries=["q"], found=1)
    assert "Read 1 of 1 page found across 1 search." in report


def test_the_one_line_result_counts_sites_not_pages():
    notes = [
        note("https://a.test/1"),
        note("https://a.test/2"),
        note("https://b.test/1"),
        note("https://dead.test/1", ok=False),
    ]
    assert one_line_result(notes, found=4) == "read 3 of 4 pages across 2 sites"


# --- reading one page -----------------------------------------------------------

def test_a_reader_that_found_nothing_is_recognised_however_it_phrased_it():
    assert is_empty_note("NOTHING RELEVANT")
    assert is_empty_note("  nothing relevant.  ")
    assert is_empty_note("")
    assert not is_empty_note("The page says the COP is 3.4.")


def test_the_synthesis_prompt_numbers_only_the_pages_that_were_read():
    # A model can only cite what it was shown. Numbering a failed fetch would
    # hand it a number pointing at nothing.
    prompt = synthesis_prompt(
        "q",
        [
            note("https://a.test/1"),
            note("https://dead.test/1", ok=False, error="404"),
            note("https://b.test/1"),
        ],
    )
    assert "[1] title of https://a.test/1" in prompt
    assert "[2] title of https://b.test/1" in prompt
    assert "dead.test" not in prompt


def test_every_prompt_that_carries_a_page_says_the_page_is_data():
    from jarvis.integrations.research.plan import note_prompt

    prompt = note_prompt("q", src("https://a.test/1"), "<untrusted>text</untrusted>")
    assert "untrusted" in prompt.lower()
    assert "ignored" in prompt.lower() or "ignore" in prompt.lower()


import json  # noqa: E402  (used by the cap test above)


def test_two_bare_lines_are_a_list_and_one_is_a_sentence():
    """The line between "the model answered in plain text" and "the model chatted".

    A model that ignores the JSON instruction usually returns several bare
    lines, which are queries. One bare line is almost always conversation, and
    searching for "Sure, here you go!" spends a whole angle of the question on
    nothing.
    """
    assert parse_queries("heat pump cop\nheat pump noise", question="q") == [
        "heat pump cop",
        "heat pump noise",
    ]
    assert parse_queries("Certainly, I can help with that", question="q") == ["q"]


def test_a_marked_list_of_one_is_still_a_list():
    assert parse_queries("- the only search", question="q") == ["the only search"]
