#!/usr/bin/env bash
# M29 — the suite runs against the real containers. Written when the milestone is built; until then it fails,
# which is what an unbuilt milestone is supposed to do.
source "$(dirname "$0")/lib.sh"
verify_begin "M29" "the suite runs against the real containers"
use_venv
# Deliberately not a marker file: a check that passes when a file exists is a
# check anybody can satisfy with `touch`. An unbuilt milestone fails, and the
# failure names where its scope is written down.
_v_fail "M29 is not built yet — see its scope in MILESTONES.md" ""
verify_end
