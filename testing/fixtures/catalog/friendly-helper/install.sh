#!/bin/sh
# The payload's own "install hook". NOTHING IN JARVIS EVER RUNS THIS — a skill
# folder is read, never executed. It is here so the test can assert that: the
# file lands, it is named in the approval prompt as a program, and the marker
# below never appears.
echo "INSTALL-HOOK-RAN" > /tmp/jarvis-catalog-probe-should-not-exist
