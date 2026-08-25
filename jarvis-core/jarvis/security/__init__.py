"""The rules that hold when everything else is compromised.

Three things live here, and they share one assumption: **prompt injection is
unsolved.** Not mitigated, not mostly-handled — unsolved. So nothing in this
package tries to detect a malicious instruction. Each piece instead removes a
way for one to matter.

* :mod:`quarantine` — every byte from outside is wrapped as data and stripped
  of the control literals that would let it forge a role boundary against a
  local model. A page cannot say `<|im_start|>system`.
* :mod:`secrets` — the values that must never be written down, and one function
  that takes them back out of anything about to be persisted.
* The escalation in `llm/tools.py`: a turn that has read external content
  cannot silently take a state-changing action, whatever the content asked for.

`docs/THREAT_MODEL.md` says what this system is actually defending, from whom,
and — the part most threat models leave out — what it does not defend at all.
"""

from __future__ import annotations

__all__ = ["quarantine", "secrets"]
