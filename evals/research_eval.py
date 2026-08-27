#!/usr/bin/env python3
"""Does research produce a report anybody can check?

A research engine is easy to make look good: a language model will write a
confident, well-structured, cited-looking answer to any question, from nothing.
So this eval asks the two questions that separate that from research —

    Did it READ anything?   (distinct sources, cited, and the links resolve)
    Is what it says TRUE?   (the facts are in pages this repository wrote)

— and it answers both against a real jarvis-core, through the real search
client, fetcher, reader and writer.

Two backends:

    --backend fixture   a small web served from `testing/live/fixtures/`, with
                        a SearXNG-shaped search in front of it. Facts are
                        checkable because we wrote the pages. This is the one
                        that runs offline, on any machine, in CI.
    --backend live      the operator's SearXNG and the open web. The facts
                        cannot be pinned (the web changes), so this checks the
                        shape: a report per question, `min_sources` distinct
                        cited sources, and every link resolving. It needs
                        `SEARXNG_URL`; without one it says so and stops rather
                        than pretending.

    python3 evals/research_eval.py --backend fixture --out .verify/research
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from testing.harness import Harness, JarvisClient  # noqa: E402
from testing.live.browser_service import SharedBrowser  # noqa: E402
from testing.live.fixture_browser import Browser  # noqa: E402
from testing.live.fixture_search import Search  # noqa: E402
from testing.live.fixture_site import SITES, Site, pages_for  # noqa: E402

QUESTIONS = REPO / "evals/research_questions.yaml"
ENV_FILE = REPO / ".env"


def load_env() -> None:
    """Read `.env` for the model server, when the caller has not exported it.

    Without this the harness falls back to its FAKE model, the eval runs in one
    second per question, every report is the fake's default sentence, and the
    result reads as "research is broken" rather than as "nobody told it where
    the model is". A research eval against a scripted model measures the
    script.
    """
    if os.environ.get("LLM_URL") or not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
CITATION_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)]+)\)")


class Failed(Exception):
    """The run did not hold up."""


def load_questions() -> list[dict]:
    data = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8")) or {}
    rows = data.get("questions") or []
    if not rows:
        raise Failed(f"{QUESTIONS} lists no questions")
    return rows


async def run_one(client, question: str, mode: str, timeout: float = 600.0) -> dict:
    """Start one research run and wait for it, through the API a client uses."""
    started = await client.call_service_rest(
        "research", "run", {"question": question, "mode": mode}, return_response=True
    )
    payload = started.get("service_response") or started
    task_id = payload.get("task_id") or (payload.get("task") or {}).get("id")
    if not task_id:
        raise Failed(f"research.run returned no task id: {started}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        answer = await client.command("jarvis/tasks/get", task_id=task_id)
        task = answer["task"]
        if task.get("finished"):
            return task
        await asyncio.sleep(1.0)
    raise Failed(f"the run for {question!r} did not finish within {timeout:g}s")


def links_in(report: str) -> list[str]:
    return CITATION_LINK.findall(report or "")


def resolves(url: str, timeout: float = 15.0) -> bool:
    """Does the link actually go anywhere? A citation to a 404 is not one."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as err:
        # Some servers refuse HEAD; a GET that works is still a resolvable link.
        if err.code in (403, 405):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as response:
                    return 200 <= response.status < 400
            except Exception:  # noqa: BLE001
                return False
        return False
    except Exception:  # noqa: BLE001 - unreachable is unresolvable
        return False


def check_report(row: dict, task: dict, *, backend: str) -> list[str]:
    """Everything wrong with one report. Empty means it held up."""
    problems: list[str] = []
    report = str(task.get("result") or "")
    if task.get("status") != "done":
        problems.append(f"the run ended {task.get('status')}: {task.get('error')}")
    if "## Sources" not in report:
        problems.append("the report cites nothing")

    links = links_in(report)
    distinct = {link.split("//", 1)[-1].split("/", 1)[0] for link in links}
    if len(distinct) < int(row.get("min_sources") or 1):
        problems.append(
            f"{len(distinct)} distinct source(s), expected at least {row.get('min_sources')}"
        )
    for link in links:
        if not resolves(link):
            problems.append(f"a citation does not resolve: {link}")

    # The facts. Only in fixture mode: we wrote those pages, and the open web
    # changes under a claim like "2.5 bar".
    if backend == "fixture":
        for needed in row.get("must_contain") or []:
            if needed.lower() not in report.lower():
                problems.append(f"the report does not contain {needed!r}")
        expected_source = str(row.get("expect_source") or "")
        if expected_source and expected_source not in report:
            problems.append(f"the page that has the answer was not cited: {expected_source}")
    return problems


async def evaluate(harness: Harness, rows: list[dict], backend: str, out: Path) -> int:
    client = JarvisClient(harness.base_url, harness.token, timeout=900)
    await client.connect()
    results: list[dict] = []
    try:
        for row in rows:
            question = str(row["question"])
            mode = str(row.get("mode") or "deep")
            print(f"  · {question}  [{mode}]", flush=True)
            started = time.monotonic()
            try:
                task = await run_one(client, question, mode)
                problems = check_report(row, task, backend=backend)
            except Failed as err:
                task, problems = {}, [str(err)]
            seconds = time.monotonic() - started
            results.append(
                {
                    "question": question,
                    "mode": mode,
                    "ok": not problems,
                    "problems": problems,
                    "seconds": round(seconds, 1),
                    "sources": len({
                        link.split("//", 1)[-1].split("/", 1)[0]
                        for link in links_in(str(task.get("result") or ""))
                    }),
                    "report": str(task.get("result") or "")[:8000],
                }
            )
            print(
                f"    {'ok  ' if not problems else 'FAIL'} {seconds:.0f}s, "
                f"{results[-1]['sources']} source(s)"
                + ("" if not problems else f" — {problems[0]}"),
                flush=True,
            )
    finally:
        await client.aclose()

    out.mkdir(parents=True, exist_ok=True)
    (out / "research_eval.json").write_text(
        json.dumps({"backend": backend, "results": results}, indent=2) + "\n"
    )
    passed = sum(1 for row in results if row["ok"])
    print(f"\nresearch eval ({backend}): {passed}/{len(results)} questions held up")
    print(f"details: {out / 'research_eval.json'}")
    return 0 if passed == len(results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out", default=".verify/research")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    rows = load_questions()
    out = Path(args.out)

    load_env()
    if not os.environ.get("LLM_URL") or not os.environ.get("LLM_MODEL"):
        print(
            "research eval: LLM_URL and LLM_MODEL are not set, and there is no .env "
            "to read them from. This eval measures what a REAL model wrote from what "
            "it really read; against a scripted one it would be measuring the script.",
            file=sys.stderr,
        )
        return 2

    sites: list[Site] = []
    search: Search | None = None
    browser: Browser | None = None
    if args.backend == "fixture":
        # More than one site on purpose: a run that can only read one host
        # cannot exercise the per-domain cap, and a claim cannot be
        # corroborated by a second source that does not exist.
        # Each site on its own loopback ADDRESS, not just its own port: the
        # per-domain cap and the cross-check both key on the host, and two
        # ports on one host are one operator in the real world — so two
        # fixture sites sharing 127.0.0.1 would be one source wearing two hats.
        sites = [
            Site(host=f"127.0.0.{index + 2}", pages=pages_for(name)).start()
            for index, name in enumerate(SITES)
        ]
        by_name = dict(zip(SITES, (site.url for site in sites)))
        search = Search(by_name).start()
        searxng = search.url
        # The REAL browser if one is running, exactly as the live rig does
        # (M31): two of the questions below are answered by a PDF and by a
        # table, and the stand-in can read neither. It says which it got, so a
        # number from this eval always names the thing that produced it.
        shared = SharedBrowser()
        fetcher = shared.start()
        browser_token = shared.token
        if fetcher:
            fetch_kind = "the running jarvis-browser"
        else:
            browser = Browser([site.url for site in sites]).start()
            fetcher = browser.url
            browser_token = ""
            fetch_kind = f"the fixture stand-in ({shared.why})"
        print(
            "research eval: fixture web at "
            + ", ".join(f"{name} {url}" for name, url in by_name.items())
            + f", search at {searxng}, fetch through {fetch_kind} at {fetcher}"
        )
    else:
        searxng = os.environ.get("SEARXNG_URL", "").strip()
        fetcher = os.environ.get("BROWSER_URL", "http://127.0.0.1:8210").strip()
        browser_token = os.environ.get("JARVIS_BROWSER_TOKEN", "").strip()
        if not searxng:
            print(
                "research eval (live): SEARXNG_URL is not set, so there is no search "
                "engine to research with.\n"
                "This is the Scripted claim in docs/verification.md: start SearXNG "
                "(`docker compose --profile search up -d searxng`) and set SEARXNG_URL, "
                "then run this again. It is not run on this host — see BLOCKERS.md.",
                file=sys.stderr,
            )
            return 2
        print(f"research eval: live, against {searxng}")

    harness = Harness(
        work_dir=str(out / "harness"),
        keep=True,
        # The model has to be real: the whole question is whether what it wrote
        # is supported by what it read, and a scripted answer would be
        # measuring the script.
        model=os.environ.get("LLM_MODEL", ""),
        ollama_url=os.environ.get("LLM_URL", ""),
        search_url=searxng,
        browser_url=fetcher,
        browser_token=browser_token,
        # The cross-encoder that decides which pages get read, when one is
        # running. Its effect is exactly what this eval measures.
        rerank_url=os.environ.get("RERANK_URL", ""),
        rerank_model=os.environ.get(
            "RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        ),
    )
    try:
        harness.start()
    except Exception as err:  # noqa: BLE001
        print(f"could not start the harness: {err}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(evaluate(harness, rows, args.backend, out))
    except Failed as err:
        print(f"\nFAILED: {err}", file=sys.stderr)
        return 1
    finally:
        harness.stop(cleanup=not args.keep)
        # Give the operator's browser back the way it was found: borrowing it
        # put the fixture web's addresses in their LAN exemption.
        if args.backend == "fixture":
            shared.stop()
        if browser is not None:
            browser.stop()
        if search is not None:
            search.stop()
        for site in sites:
            site.stop()


if __name__ == "__main__":
    raise SystemExit(main())
