#!/usr/bin/env bash
# M34 — the vector store, decided. Written when the milestone is built; until then it fails,
# which is what an unbuilt milestone is supposed to do.
source "$(dirname "$0")/lib.sh"
verify_begin "M34" "the vector store, decided"
use_venv
# Deliberately not a marker file: a check that passes when a file exists is a
# check anybody can satisfy with `touch`. An unbuilt milestone fails, and the
# failure names where its scope is written down.
_v_fail "M34 is not built yet — see its scope in MILESTONES.md" ""
verify_end
