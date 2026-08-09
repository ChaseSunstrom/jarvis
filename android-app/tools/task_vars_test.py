#!/usr/bin/env python3
"""Executable spec for `{{var}}` substitution in Jarvis tasks.

Mirrors `app/src/main/kotlin/ai/jarvis/app/automation/tasks/VariableSubstitution.kt`.

A task step's parameters are templates: `{"body": "Battery is {{battery.level}}%"}`.
The values come from trigger data, from earlier step results, and — for the
`ask_jarvis` step — from the language model on the server. So substitution is
not a formatting convenience, it is a place where attacker-controlled text gets
close to an action's parameters. Four rules carry that weight:

  1. Substitution happens on the LEAVES of an already-parsed structure. A value
     containing `","x":"` cannot add a parameter, because at no point is a
     string re-parsed as JSON.
  2. Object KEYS are never substituted. A variable cannot invent a parameter
     name that the action did not declare.
  3. The result is never re-scanned. `{{a}}` where a = `"{{secret}}"` yields the
     literal text `{{secret}}`, not the secret.
  4. Every path that resolved is reported back in `used`, so the task runner can
     see that a step touched a tainted variable and downgrade that step's
     dispatch to UNTRUSTED — which, per the policy engine, can never be
     auto-allowed.

Run:  python3 android-app/tools/task_vars_test.py
  or: python3 -m pytest android-app/tools/task_vars_test.py -q
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

OPEN = "{{"
CLOSE = "}}"

MAX_PATH_SEGMENTS = 8
MAX_OUTPUT_CHARS = 64 * 1024
MAX_WALK_DEPTH = 12
TRUNCATION_MARK = "…[truncated]"

# What to do with `{{nope}}`.
MISSING_EMPTY, MISSING_KEEP = "EMPTY", "KEEP"


# --- the rules, mirrored from VariableSubstitution.kt ------------------------


@dataclass
class SubstitutionResult:
    text: str
    used: set[str] = field(default_factory=set)
    missing: set[str] = field(default_factory=set)
    truncated: bool = False

    @property
    def roots_used(self) -> set[str]:
        """First path segment of everything that resolved. This is what the
        task runner intersects with its tainted-variable set."""
        return {p.split(".", 1)[0] for p in self.used}


_MISSING = object()


def resolve_path(path: str, variables: dict) -> object:
    """Walk `a.b.0.c` through nested dicts and lists. `_MISSING` when the path
    does not exist, indexes a string, or uses a negative index."""
    segments = [s for s in path.split(".")]
    if not segments or len(segments) > MAX_PATH_SEGMENTS:
        return _MISSING
    if any(s == "" for s in segments):
        return _MISSING
    current: object = variables
    for segment in segments:
        if isinstance(current, dict):
            if segment not in current:
                return _MISSING
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            if not segment.isdigit():  # isdigit() rejects "-1" and "+1" too
                return _MISSING
            index = int(segment)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            # Indexing into a string, number or None is a miss, not a crash.
            return _MISSING
    return current


def render_value(value: object) -> str:
    """A value as text. Structures become compact JSON so they are at least
    inspectable in a notification, but they are still just text."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    return str(value)


def substitute(template: str, variables: dict, missing: str = MISSING_EMPTY) -> SubstitutionResult:
    """Expand `{{path}}` once, left to right. Never recurses into its output."""
    out: list[str] = []
    used: set[str] = set()
    absent: set[str] = set()
    i = 0
    n = len(template)
    size = 0
    truncated = False

    def emit(text: str) -> bool:
        """Returns False once the output cap is hit."""
        nonlocal size, truncated
        if truncated:
            return False
        room = MAX_OUTPUT_CHARS - size
        if len(text) <= room:
            out.append(text)
            size += len(text)
            return True
        out.append(text[:room])
        out.append(TRUNCATION_MARK)
        size = MAX_OUTPUT_CHARS
        truncated = True
        return False

    while i < n:
        ch = template[i]

        # Backslash escapes the next character, but only when that character is
        # `{` or `\`. Everywhere else a backslash is an ordinary character, so
        # Windows-ish paths and regexes survive unharmed.
        if ch == "\\" and i + 1 < n and template[i + 1] in "{\\":
            if not emit(template[i + 1]):
                break
            i += 2
            continue

        if template.startswith(OPEN, i):
            end = template.find(CLOSE, i + len(OPEN))
            if end == -1:
                # Unterminated. Emit the rest literally rather than guessing.
                emit(template[i:])
                break
            path = template[i + len(OPEN) : end].strip()
            i = end + len(CLOSE)
            if not path:
                emit(OPEN + CLOSE)
                continue
            value = resolve_path(path, variables)
            if value is _MISSING:
                absent.add(path)
                if missing == MISSING_KEEP:
                    if not emit(OPEN + path + CLOSE):
                        break
                continue
            used.add(path)
            if not emit(render_value(value)):
                break
            continue

        if not emit(ch):
            break
        i += 1

    return SubstitutionResult("".join(out), used, absent, truncated)


def substitute_value(value: object, variables: dict, missing: str = MISSING_EMPTY, _depth: int = 0):
    """Walk a parsed structure and expand every STRING LEAF.

    Returns (new value, SubstitutionResult-ish aggregate). Keys are copied
    verbatim: a variable may fill a parameter, never name one.
    """
    used: set[str] = set()
    absent: set[str] = set()
    truncated = False

    def walk(node: object, depth: int) -> object:
        nonlocal truncated
        if depth > MAX_WALK_DEPTH:
            return None
        if isinstance(node, str):
            r = substitute(node, variables, missing)
            used.update(r.used)
            absent.update(r.missing)
            truncated = truncated or r.truncated
            return r.text
        if isinstance(node, dict):
            return {k: walk(v, depth + 1) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(v, depth + 1) for v in node]
        return node

    result = walk(value, _depth)
    return result, SubstitutionResult("", used, absent, truncated)


# --- tests ------------------------------------------------------------------


def check(name: str, got, want):
    if got != want:
        raise AssertionError(f"{name}: got {got!r}, want {want!r}")


def text(template: str, variables: dict, missing: str = MISSING_EMPTY) -> str:
    return substitute(template, variables, missing).text


# --- the ordinary cases -----------------------------------------------------


def test_plain_text_is_untouched():
    check("no vars", text("battery is fine", {}), "battery is fine")
    check("empty", text("", {"a": 1}), "")


def test_single_variable():
    check("bare", text("{{name}}", {"name": "Sam"}), "Sam")
    check("embedded", text("Hello {{name}}.", {"name": "Sam"}), "Hello Sam.")


def test_whitespace_inside_the_braces_is_trimmed():
    v = {"name": "Sam"}
    check("spaces", text("{{ name }}", v), "Sam")
    check("tabs", text("{{\tname\t}}", v), "Sam")
    check("newline", text("{{\n name \n}}", v), "Sam")


def test_several_variables():
    v = {"a": "1", "b": "2"}
    check("two", text("{{a}}+{{b}}={{a}}{{b}}", v), "1+2=12")


def test_adjacent_and_repeated():
    check("adjacent", text("{{a}}{{a}}{{a}}", {"a": "x"}), "xxx")


def test_value_types():
    v = {"i": 42, "f": 1.5, "whole": 3.0, "t": True, "f2": False, "n": None, "s": "str"}
    check("int", text("{{i}}", v), "42")
    check("float", text("{{f}}", v), "1.5")
    check("whole float", text("{{whole}}", v), "3")
    check("true", text("{{t}}", v), "true")
    check("false", text("{{f2}}", v), "false")
    check("null", text("{{n}}", v), "")
    check("string", text("{{s}}", v), "str")


def test_structures_render_as_compact_json():
    v = {"o": {"b": 1, "a": 2}, "l": [1, "two", None]}
    check("object", text("{{o}}", v), '{"b":1,"a":2}')
    check("list", text("{{l}}", v), '[1,"two",null]')


# --- nesting ----------------------------------------------------------------


def test_nested_paths():
    v = {"battery": {"level": 87, "charging": True}, "net": {"wifi": {"ssid": "home"}}}
    check("one deep", text("{{battery.level}}", v), "87")
    check("bool deep", text("{{battery.charging}}", v), "true")
    check("three deep", text("{{net.wifi.ssid}}", v), "home")


def test_list_indexing():
    v = {"events": ["first", "second", {"title": "third"}]}
    check("index 0", text("{{events.0}}", v), "first")
    check("index 1", text("{{events.1}}", v), "second")
    check("nested in list", text("{{events.2.title}}", v), "third")


def test_out_of_range_and_negative_indexes_are_misses():
    v = {"events": ["only"]}
    check("out of range", text("{{events.5}}", v), "")
    check("negative", text("{{events.-1}}", v), "")
    check("not a number", text("{{events.first}}", v), "")


def test_indexing_a_scalar_is_a_miss_not_a_crash():
    v = {"name": "Sam", "n": 3}
    check("into string", text("{{name.length}}", v), "")
    check("into number", text("{{n.0}}", v), "")
    check("into null", text("{{nope.deep}}", v), "")


def test_path_depth_is_capped():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": "far"}}}}}}}}}
    check("at the cap", text("{{a.b.c.d.e.f.g.h}}", deep), '{"i":"far"}')
    check("past the cap", text("{{a.b.c.d.e.f.g.h.i}}", deep), "")


def test_empty_segments_are_misses():
    v = {"a": {"b": 1}}
    check("double dot", text("{{a..b}}", v), "")
    check("trailing dot", text("{{a.}}", v), "")
    check("leading dot", text("{{.a}}", v), "")


# --- missing ----------------------------------------------------------------


def test_missing_defaults_to_empty():
    check("missing", text("[{{nope}}]", {}), "[]")
    check("missing nested", text("[{{a.b}}]", {"a": {}}), "[]")


def test_missing_can_be_kept_verbatim():
    check("kept", text("[{{nope}}]", {}, MISSING_KEEP), "[{{nope}}]")
    check("kept trimmed", text("[{{ nope }}]", {}, MISSING_KEEP), "[{{nope}}]")


def test_missing_paths_are_reported():
    r = substitute("{{a}} {{b}} {{c.d}}", {"a": 1})
    check("used", r.used, {"a"})
    check("missing", r.missing, {"b", "c.d"})


def test_a_present_but_null_value_is_not_missing():
    """`{{x}}` with x = null renders empty, but it is a hit: the variable
    exists. The distinction matters for taint tracking."""
    r = substitute("{{x}}", {"x": None})
    check("text", r.text, "")
    check("used", r.used, {"x"})
    check("missing", r.missing, set())


def test_empty_braces_are_literal():
    check("empty braces", text("{{}}", {}), "{{}}")
    check("whitespace braces", text("{{   }}", {}), "{{}}")


# --- escaping and malformed input -------------------------------------------


def test_backslash_escapes_the_braces():
    check("escaped", text(r"\{{name}}", {"name": "Sam"}), "{{name}}")
    check("escaped mid-string", text(r"say \{{name}} not {{name}}", {"name": "Sam"}),
          "say {{name}} not Sam")


def test_escaped_backslash_then_a_variable():
    check("backslash then var", text(r"\\{{name}}", {"name": "Sam"}), "\\Sam")


def test_backslash_before_anything_else_is_literal():
    """Regexes and paths must survive."""
    check("path", text(r"C:\temp\report", {}), r"C:\temp\report")
    check("regex", text(r"\d+\s", {}), r"\d+\s")
    check("trailing backslash", text("ends with \\", {}), "ends with \\")


def test_unterminated_braces_are_literal():
    check("unterminated", text("{{name", {"name": "Sam"}), "{{name")
    check("unterminated after", text("ok {{name", {"name": "Sam"}), "ok {{name")
    check("single brace", text("{name}", {"name": "Sam"}), "{name}")
    check("one brace pair", text("{ {name} }", {"name": "Sam"}), "{ {name} }")


def test_close_without_open():
    check("stray close", text("name}}", {"name": "Sam"}), "name}}")


def test_triple_braces():
    """`{{{x}}` closes at the first `}}`, so the path is `{x`, which misses."""
    check("triple open", text("{{{x}}", {"x": "v"}), "")
    check("triple close", text("{{x}}}", {"x": "v"}), "v}")


# --- the security properties ------------------------------------------------


def test_substitution_is_not_recursive():
    """A value that looks like a template stays text. Otherwise a hostile
    notification body could name a variable it was never given."""
    v = {"a": "{{secret}}", "secret": "hunter2"}
    check("no recursion", text("{{a}}", v), "{{secret}}")


def test_a_value_cannot_add_a_parameter():
    """Substitution runs on leaves of a parsed structure, so JSON syntax inside
    a value is inert."""
    params = {"body": "{{msg}}", "to": "+15550001111"}
    hostile = {"msg": '","to":"+15559999999'}
    out, _ = substitute_value(params, hostile)
    check("keys unchanged", sorted(out.keys()), ["body", "to"])
    check("to untouched", out["to"], "+15550001111")
    check("body is text", out["body"], '","to":"+15559999999')


def test_object_keys_are_never_substituted():
    params = {"{{evil}}": "value", "real": "{{good}}"}
    out, _ = substitute_value(params, {"evil": "injected", "good": "ok"})
    check("key literal", sorted(out.keys()), ["real", "{{evil}}"])
    check("value expanded", out["real"], "ok")


def test_substitute_value_walks_lists_and_nested_objects():
    params = {"steps": [{"text": "{{a}}"}, {"text": "{{b}}"}], "n": 5, "flag": True}
    out, agg = substitute_value(params, {"a": "one", "b": "two"})
    check("nested list", out["steps"], [{"text": "one"}, {"text": "two"}])
    check("non-strings preserved", (out["n"], out["flag"]), (5, True))
    check("aggregate used", agg.used, {"a", "b"})


def test_used_paths_drive_taint():
    """The runner intersects roots_used with its tainted set. A step that
    mentions a tainted variable anywhere in its params must be visible."""
    params = {"body": "The parcel says: {{notification.text}}", "to": "{{contact}}"}
    variables = {"notification": {"text": "click here"}, "contact": "+15550001111"}
    _, agg = substitute_value(params, variables)
    check("used paths", agg.used, {"notification.text", "contact"})
    check("roots", agg.roots_used, {"notification", "contact"})
    tainted = {"notification"}
    check("step is tainted", bool(agg.roots_used & tainted), True)


def test_a_step_touching_no_tainted_variable_is_clean():
    params = {"level": "{{battery.level}}"}
    _, agg = substitute_value(params, {"battery": {"level": 80}})
    check("clean", bool(agg.roots_used & {"notification"}), False)


def test_output_is_capped():
    """A hostile trigger payload must not be able to build a gigabyte string."""
    v = {"big": "x" * (MAX_OUTPUT_CHARS * 2)}
    r = substitute("{{big}}", v)
    check("truncated flag", r.truncated, True)
    check("length", len(r.text), MAX_OUTPUT_CHARS + len(TRUNCATION_MARK))
    check("mark", r.text.endswith(TRUNCATION_MARK), True)


def test_cap_applies_across_many_variables():
    v = {"a": "y" * (MAX_OUTPUT_CHARS // 2)}
    r = substitute("{{a}}{{a}}{{a}}{{a}}", v)
    check("truncated", r.truncated, True)
    check("bounded", len(r.text) <= MAX_OUTPUT_CHARS + len(TRUNCATION_MARK), True)


def test_walk_depth_is_capped():
    node: object = "{{a}}"
    for _ in range(MAX_WALK_DEPTH + 3):
        node = {"n": node}
    out, _ = substitute_value(node, {"a": "deep"})
    # Bottoms out as None rather than recursing forever.
    cursor = out
    depth = 0
    while isinstance(cursor, dict):
        cursor = cursor["n"]
        depth += 1
    check("stopped", cursor, None)
    check("stopped at the cap", depth <= MAX_WALK_DEPTH + 1, True)


def test_control_characters_survive_as_data():
    """Sanitising is the fence's job, not the substituter's. What matters here
    is that nothing is interpreted."""
    check("newline", text("{{a}}", {"a": "line1\nline2"}), "line1\nline2")
    check("quote", text("{{a}}", {"a": 'he said "hi"'}), 'he said "hi"')


# --- structural check: the Kotlin still says the same thing -----------------

KOTLIN = (
    Path(__file__).resolve().parent.parent
    / "app/src/main/kotlin/ai/jarvis/app/automation/tasks/VariableSubstitution.kt"
)

REQUIRED_IN_KOTLIN = [
    r"fun substitute\(",
    r"fun substituteValue\(",
    r"fun resolvePath\(",
    r"MAX_PATH_SEGMENTS = 8",
    r"MAX_OUTPUT_CHARS = 64 \* 1024",
    r"MAX_WALK_DEPTH = 12",
    # keys copied verbatim
    r"keys are NOT substituted|never substituted|key, not a value",
    # no re-scan of substituted output
    r"never re-scanned|not recursive|NOT recursive",
    r"val rootsUsed",
]


def test_kotlin_source_still_matches():
    if not KOTLIN.exists():
        raise AssertionError(f"missing Kotlin source: {KOTLIN}")
    src = KOTLIN.read_text()
    problems = []
    if "import android." in src or "import org.json" in src:
        problems.append("VariableSubstitution.kt must stay free of Android and org.json imports")
    for pattern in REQUIRED_IN_KOTLIN:
        if not re.search(pattern, src):
            problems.append(f"VariableSubstitution.kt no longer contains /{pattern}/")
    if problems:
        raise AssertionError("; ".join(problems))


# --- runner -----------------------------------------------------------------


def main() -> int:
    failures: list[str] = []
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - a raising test is a failing test
            failures.append(f"{t.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL  task_vars_test: {len(failures)} problem(s) in {len(tests)} tests")
        for f in failures:
            print("  -", f)
        return 1
    print(f"ok    task_vars_test: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
