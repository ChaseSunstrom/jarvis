"""jarvis-browser — sandboxed web crawling + browser automation for Jarvis.

Everything the LLM can reach on the open web comes through here. The service
holds two lines that must never be crossed:

  1. Fetched page content is DATA. It is returned fenced in
     ``<untrusted_web_content>`` and no code path turns it into an action.
  2. Writing to a page (click/type/submit) is gated: the domain must be on an
     explicit ``act_allowlist``, and any sensitive step needs a human approval
     carrying a SEPARATE secret (see ``safety.ApprovalGate``, which mirrors
     ``jarvis-orchestrator/app/exec_gate.py``).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
