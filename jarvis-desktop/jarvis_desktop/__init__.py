"""jarvis-desktop — the Jarvis device agent for Linux, macOS and Windows.

The same automation capability as the phone app, with the same policy model
enforced in the same order. The LLM runs on the server; this process decides,
locally and outside the model, what is actually allowed to happen to the
machine it runs on.

Read in this order:

* :mod:`jarvis_desktop.policy`   — the tier/decision model. Start here.
* :mod:`jarvis_desktop.actions`  — what can be done, and the one door it goes through.
* :mod:`jarvis_desktop.consent`  — the Tier-3 prompt.
* :mod:`jarvis_desktop.channel`  — the wire protocol.
* :mod:`jarvis_desktop.audit`    — what was done, written down.
"""

from .policy import ActionTier, Decision, PolicyEngine, TrustLevel, UserPolicy

__version__ = "0.1.0"

__all__ = [
    "ActionTier",
    "Decision",
    "PolicyEngine",
    "TrustLevel",
    "UserPolicy",
    "__version__",
]
