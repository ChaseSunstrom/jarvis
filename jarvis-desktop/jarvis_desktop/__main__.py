"""``python -m jarvis_desktop`` — run the agent, or inspect what it would do.

Subcommands::

    python -m jarvis_desktop run --server ws://jarvis.lan:8080 --token ...
    python -m jarvis_desktop tiers            # the local action table
    python -m jarvis_desktop policy list|set|panic
    python -m jarvis_desktop audit --limit 50
    python -m jarvis_desktop cron "*/15 9-17 * * mon-fri"
    python -m jarvis_desktop doctor           # what works on this machine
    python -m jarvis_desktop status --watch   # what the running agent is doing

``run`` is the default, so ``python -m jarvis_desktop --server ... --token ...``
does the obvious thing.

``doctor`` and ``status`` answer two different questions and are both worth
having: doctor is about this *machine* and can be run with nothing going on,
status is about the *process* and is empty without one.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .actions.builtins import build_context, build_registry
from .audit import AuditLog
from .channel import DeviceChannel
from .companion import CompanionHandler, build_asker
from .config import Config, load_config, normalize_server_url
from .consent import build_gateway
from .policy import ActionTier, PolicyStore, UserPolicy
from .presence import PresenceReporter
from .status import StatusSnapshot, StatusWriter
from .triggers import CronSchedule, TriggerManager, build_triggers, next_fire_times

_LOGGER = logging.getLogger("jarvis_desktop")


def _setup_logging(verbosity: int, log_file: str | None = None) -> None:
    level = logging.WARNING if verbosity < 0 else logging.INFO if verbosity == 0 else logging.DEBUG
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        try:
            handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
        except OSError as exc:
            print(f"could not open the log file: {exc}", file=sys.stderr)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # The token never reaches a log line, but the websockets library is chatty
    # about frame contents at DEBUG, and frames carry it.
    logging.getLogger("websockets").setLevel(logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    # The global flags exist twice on purpose, so that `run -q` works as well as
    # `-q run`.
    #
    # The top-level copies carry the real defaults. The subparser copies default
    # to SUPPRESS, so an unused flag leaves no attribute behind and cannot
    # overwrite the value the top-level parser already read — `-v tiers` would
    # otherwise silently lose its -v.
    #
    # They are two independent sets rather than one shared `parents=` group
    # because argparse's `parents` shares the *same action objects*, and
    # `set_defaults` mutates `action.default` in place: one call on the top-level
    # parser would rewrite the defaults of every subparser as a side effect.
    parser = argparse.ArgumentParser(
        prog="python -m jarvis_desktop",
        description="The Jarvis desktop agent: automation for this machine, "
        "with policy enforced locally.",
    )
    parser.add_argument("--version", action="version", version=f"jarvis-desktop {__version__}")
    parser.add_argument("-c", "--config", help="path to config.json", default=None)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--log-file", default=None)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "-v", "--verbose", action="count", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    common.add_argument(
        "-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    common.add_argument("--log-file", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    subs = parser.add_subparsers(dest="command")

    def sub(name: str, help_text: str) -> argparse.ArgumentParser:
        return subs.add_parser(name, help=help_text, parents=[common])

    run = sub("run", "connect to jarvis-core and serve commands")
    run.add_argument("--server", help="ws://host:8080 (or a full /api/websocket URL)")
    run.add_argument("--token", help="long-lived access token (prefer --token-file or JARVIS_TOKEN)")
    run.add_argument("--token-file", help="read the token from this file")
    run.add_argument("--workspace", help="directory file actions are confined to")
    run.add_argument("--pin-host", help="refuse to connect to any other hostname")
    run.add_argument(
        "--headless",
        action="store_true",
        help="no human is watching: deny everything that needs a prompt",
    )
    run.add_argument("--once", action="store_true", help="run one session, then exit")

    sub("tiers", "print the local action table")

    policy = sub("policy", "inspect and change the local policy")
    policy.add_argument("action", choices=["list", "set", "clear", "panic", "enable", "disable"])
    policy.add_argument("target", nargs="?", help="action id, or on|off for panic")
    policy.add_argument(
        "value", nargs="?", choices=["allow_always", "ask", "never"], help="for `set`"
    )

    audit = sub("audit", "read the local audit log")
    audit.add_argument("--limit", type=int, default=30)
    audit.add_argument("--json", action="store_true")

    cron = sub("cron", "preview a cron expression")
    cron.add_argument("expression")
    cron.add_argument("--count", type=int, default=5)

    sub("doctor", "report what this machine can and cannot do")

    status = sub("status", "what the running agent is doing right now")
    status.add_argument(
        "--watch", action="store_true", help="redraw until interrupted"
    )
    status.add_argument(
        "--interval", type=float, default=2.0, help="seconds between redraws with --watch"
    )
    status.add_argument(
        "--limit", type=int, default=5, help="recent audit lines to show (0 for none)"
    )
    status.add_argument("--json", action="store_true")

    enrol = subs.add_parser(
        "enrol",
        parents=[common],
        help="teach Jarvis your voice from this machine",
        description=(
            "Record a few phrases and send them to jarvis-core, so it can tell "
            "your voice from anybody else's. Uses whatever recorder this "
            "machine already has (arecord, sox, ffmpeg, afrecord); if it has "
            "none, record a WAV yourself and pass --from-file."
        ),
    )
    enrol.add_argument("--from-file", help="a 16-bit WAV to send instead of recording")
    enrol.add_argument("--seconds", type=int, default=5, help="length of each sample")
    enrol.add_argument("--samples", type=int, default=0, help="how many to record (0 = the server's minimum)")
    enrol.add_argument("--recorder", default="", help="force one of the recorders")
    enrol.add_argument("--list-recorders", action="store_true", help="show what is available and stop")
    enrol.add_argument("--status", action="store_true", help="show whose voice is enrolled and stop")

    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    overrides: dict[str, Any] = {}
    if getattr(args, "server", None):
        overrides["server_url"] = normalize_server_url(args.server)
    if getattr(args, "token", None):
        overrides["token"] = args.token
    if getattr(args, "workspace", None):
        overrides["file_roots"] = [args.workspace]
    if getattr(args, "pin_host", None):
        overrides["pinned_host"] = args.pin_host
    env = dict(os.environ)
    if getattr(args, "token_file", None):
        env["JARVIS_TOKEN_FILE"] = args.token_file
        env.pop("JARVIS_TOKEN", None)
    config = load_config(args.config, env=env, overrides=overrides)
    if getattr(args, "headless", False):
        config = dataclasses.replace(config, headless_deny=True)
    return config.ensure_dirs()


# --- commands ---------------------------------------------------------------


def cmd_tiers(config: Config) -> int:
    audit = AuditLog(config.audit_path)
    registry = build_registry(config, PolicyStore(config.policy_path), audit)
    store = PolicyStore(config.policy_path)
    width = max(len(i) for i in registry.ids())
    print(f"{'action'.ljust(width)}  tier      your policy   available")
    print("-" * (width + 40))
    for action in sorted(registry.manifest(), key=lambda a: (a["tier"], a["id"])):
        policy = store.policy_for(action["id"]).value
        mark = "yes" if action["available"] else "no"
        print(
            f"{action['id'].ljust(width)}  "
            f"{action['tier_name'].ljust(8)}  {policy.ljust(12)}  {mark}"
        )
    print()
    print("Tier 1 AUTO runs without asking. Tier 2 NOTIFY asks once, then remembers")
    print("if you let it. Tier 3 CONFIRM asks every single time and cannot be")
    print("remembered - that is not configurable, by design.")
    return 0


def cmd_policy(config: Config, args: argparse.Namespace) -> int:
    store = PolicyStore(config.policy_path)
    action = args.action
    if action == "list":
        print(f"automation: {'on' if store.automation_enabled else 'OFF'}")
        print(f"panic:      {'ON (everything is denied)' if store.panic else 'off'}")
        remembered = store.all_policies()
        if not remembered:
            print("\nno remembered answers; everything is at its default (ask)")
            return 0
        print("\nremembered answers:")
        for key, value in sorted(remembered.items()):
            print(f"  {key:<24} {value.value}")
        return 0
    if action == "set":
        if not args.target or not args.value:
            print("usage: policy set <action_id> <allow_always|ask|never>", file=sys.stderr)
            return 2
        registry_tier = _tier_of(config, args.target)
        if registry_tier is None:
            print(f"no such action: {args.target}", file=sys.stderr)
            return 2
        policy = UserPolicy.from_stored(args.value)
        if policy == UserPolicy.ALLOW_ALWAYS and registry_tier == ActionTier.CONFIRM:
            print(
                f"{args.target} is Tier 3 (CONFIRM): it asks every time and that "
                "cannot be remembered. Use `never` to block it outright.",
                file=sys.stderr,
            )
            return 2
        store.set_policy(args.target, policy, registry_tier)
        print(f"{args.target} -> {policy.value}")
        return 0
    if action == "clear":
        if not args.target:
            store.clear_all_policies()
            print("cleared every remembered answer")
        else:
            store.clear_policy(args.target)
            print(f"{args.target} -> ask")
        return 0
    if action == "panic":
        if args.target in ("on", "off"):
            store.panic = args.target == "on"
        else:
            store.panic = not store.panic
        print(f"panic is now {'ON - everything is denied' if store.panic else 'off'}")
        return 0
    if action in ("enable", "disable"):
        store.automation_enabled = action == "enable"
        print(f"automation is now {'on' if store.automation_enabled else 'off'}")
        return 0
    return 2


def _tier_of(config: Config, action_id: str) -> ActionTier | None:
    registry = build_registry(config, PolicyStore(config.policy_path), AuditLog(config.audit_path))
    action = registry.get(action_id)
    return action.tier if action else None


def cmd_audit(config: Config, args: argparse.Namespace) -> int:
    log = AuditLog(config.audit_path)
    entries = log.read(limit=max(1, args.limit))
    if args.json:
        print(json.dumps([e.to_json() for e in entries], indent=2))
        return 0
    if not entries:
        print(f"nothing recorded yet ({config.audit_path})")
        return 0
    for entry in reversed(entries):
        stamp = entry.to_json()["time"]
        flag = {"ok": "  ", "denied": "!!", "error": "xx", "unsupported": "--"}.get(
            entry.status, "??"
        )
        print(
            f"{flag} {stamp}  {entry.action_id:<20} {entry.tier.name:<8} "
            f"{entry.decision.value:<6} {entry.status:<12} {entry.source}"
        )
        if entry.note:
            print(f"     {entry.note}")
    print(f"\n{config.audit_path}")
    return 0


def cmd_cron(args: argparse.Namespace) -> int:
    try:
        CronSchedule.parse(args.expression)
    except ValueError as exc:
        print(f"bad cron expression: {exc}", file=sys.stderr)
        return 2
    for stamp in next_fire_times(args.expression, count=max(1, args.count)):
        print(stamp)
    return 0


def cmd_status(config: Config, args: argparse.Namespace) -> int:
    """Print what the running agent is doing; ``--watch`` keeps printing.

    Exit code 1 when there is no agent running (missing or stale status file),
    so this doubles as a health check for a supervisor or a shell script — the
    reason people reach for a status command at all.
    """
    from .status import StatusFile, render

    store = StatusFile(config.status_path)
    log = AuditLog(config.audit_path)
    policy = PolicyStore(config.policy_path)
    limit = max(0, args.limit)

    def once() -> int:
        snapshot = store.read()
        entries = log.read(limit=limit) if limit else []
        running = snapshot is not None and not snapshot.stale()
        if args.json:
            print(
                json.dumps(
                    {
                        "running": running,
                        "status": snapshot.to_json() if snapshot else None,
                        "recent": [e.to_json() for e in entries],
                    },
                    indent=2,
                )
            )
        else:
            print(
                render(
                    snapshot,
                    entries,
                    path=config.status_path,
                    panic=policy.panic,
                    automation_enabled=policy.automation_enabled,
                )
            )
        return 0 if running else 1

    if not args.watch:
        return once()

    interval = max(0.5, args.interval)
    try:
        while True:
            if sys.stdout.isatty():
                # Home, then clear: a redraw that scrolls is not a status
                # display, it is a log. Only on a terminal, so piping `status
                # --watch` into a file does not fill it with escape codes.
                print("\x1b[H\x1b[2J", end="")
            once()
            time.sleep(interval)
    except KeyboardInterrupt:
        # ^C is how you leave a watch. It is not a failure.
        return 0


def cmd_doctor(config: Config) -> int:
    from .actions.clipboard import clipboard_backend
    from .consent import NotifyingDenyGateway, TerminalConsentGateway, TkConsentGateway
    from .status import StatusFile

    ctx = build_context(config)
    registry = build_registry(config, PolicyStore(config.policy_path), AuditLog(config.audit_path), ctx=ctx)

    print(f"jarvis-desktop {__version__}")
    print(f"  state       {config.state_dir}")
    print(f"  workspace   {', '.join(str(r) for r in ctx.scope.roots)}")
    print(f"  server      {config.server_url}")
    print(f"  device      {config.device_name} ({config.device_id})")
    print()
    print("consent backends (first usable one wins):")
    for gateway in (TkConsentGateway(), TerminalConsentGateway()):
        print(f"  {gateway.name:<12} {'available' if gateway.usable() else 'not available'}")
    print("  deny-all     always (nothing is approved without a human)")
    notice = NotifyingDenyGateway()
    print(f"               refusals are announced via {notice.notifier.name}")
    print()
    snapshot = StatusFile(config.status_path).read()
    if snapshot is None:
        print(f"status        no agent running ({config.status_path})")
    else:
        state = "stale" if snapshot.stale() else ("connected" if snapshot.connected else "up")
        print(f"status        {state}, pid {snapshot.pid} ({config.status_path})")
    print()
    print(f"clipboard     {clipboard_backend(ctx) or 'none - read/write unsupported'}")
    try:
        import psutil  # noqa: F401

        print("psutil        installed (full CPU/memory/battery detail)")
    except ImportError:
        print("psutil        missing (stdlib fallbacks in use)")
    try:
        import pyautogui  # noqa: F401

        print("pyautogui     installed")
    except Exception:  # noqa: BLE001
        print("pyautogui     missing (input automation unsupported)")
    print()
    unavailable = [a for a in registry.manifest() if not a["available"]]
    if unavailable:
        print("actions that cannot run here:")
        for action in unavailable:
            print(f"  {action['id']:<20} {action.get('unsupported_reason', '')}")
    else:
        print("every action in the table can run on this machine")
    return 0


async def cmd_run(config: Config, once: bool = False) -> int:
    if not config.token:
        _LOGGER.error(
            "no token. Pass --token-file, set JARVIS_TOKEN, or put \"token\" in the config."
        )
        return 2
    if not config.server_url:
        _LOGGER.error("no server. Pass --server ws://host:8080.")
        return 2

    policy = PolicyStore(config.policy_path)
    audit = AuditLog(config.audit_path)

    # `emit` is written before the channel it sends through because the wiring
    # below is a ring: presence needs emit, the consent gateway needs presence's
    # hook (answering a prompt is being at the machine), the registry needs the
    # gateway and the channel needs the registry. The closure reads `channel`
    # when it is called, which is long after the assignment below.
    async def emit(event: str, data: dict) -> bool:
        return await channel.emit_event(event, data)

    # --- the other direction: jarvis-core reaching the human at this desk ---
    #
    # Presence tells the server whether the user is here; companion renders
    # what it decides to send. Neither is handed the registry, the dispatcher
    # or the policy store, so a proactive message has no path to running
    # anything — that is wiring, not a rule someone has to remember.
    presence = PresenceReporter(emit)
    consent = build_gateway(
        headless_deny=config.headless_deny,
        on_interaction=presence.note_interaction,
    )
    registry = build_registry(config, policy, audit, consent=consent)
    channel = DeviceChannel(config, registry)

    triggers = TriggerManager(emit, build_triggers(config.triggers))

    status = StatusWriter(
        config.status_path,
        lambda: StatusSnapshot(
            version=__version__,
            device_id=config.device_id,
            device_name=config.device_name,
            server_url=config.server_url,
            consent_backend=consent.describe(),
            action_count=len(registry),
            connected=channel.registered,
        ),
    )

    companion = CompanionHandler(
        channel.send_frame,
        asker=build_asker(headless=config.headless_deny),
        on_interaction=presence.note_interaction,
    )
    channel.companion = companion
    channel.on_registered = presence.reconnected

    _LOGGER.info(
        "jarvis-desktop %s starting: %s -> %s, %d actions, consent via %s",
        __version__,
        config.device_name,
        config.server_url,
        len(registry),
        consent.describe(),
    )
    _LOGGER.info("%s", companion.describe())
    if policy.panic:
        _LOGGER.warning("PANIC is set: every command will be denied until you clear it")
    if not policy.automation_enabled:
        _LOGGER.warning("automation is switched off: every command will be denied")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    await triggers.start()
    await presence.start()
    await status.start()
    runner = asyncio.create_task(channel.run_forever(max_sessions=1 if once else None))
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        _LOGGER.info("shutting down")
        await channel.stop()
        await presence.stop()
        # Removes the status file: an agent that has stopped must not leave one
        # behind saying it is up. A killed agent cannot get here, which is what
        # `status` reads staleness for.
        await status.stop()
        # Cancels any question still on screen. The ledger records nothing for
        # it, so the server gets no answer and a redelivery after the next
        # connection is free to ask again.
        await companion.close()
        await triggers.stop()
        for task in (runner, stopper):
            task.cancel()
        await asyncio.gather(runner, stopper, return_exceptions=True)
    return 0


def cmd_enrol(config: Config, args: argparse.Namespace) -> int:
    """Record phrases and send them to jarvis-core's speaker profile.

    Kept in this file with the other commands, and thin: everything about
    recorders and WAV files is in `enrol.py`, and everything about the HTTP is
    in `enrol_client.py`, so this is the conversation and nothing else.
    """
    from .enrol import RECORDERS, EnrolError, find_recorder, read_wav, record_wav
    from .enrol_client import SpeakerClient, SpeakerError

    if args.list_recorders:
        found = [r for r in RECORDERS if shutil.which(r.name)]
        for recorder in RECORDERS:
            mark = "yes" if recorder in found else "no "
            print(f"  {mark}  {recorder.name.ljust(10)} {recorder.install}")
        if not found:
            print()
            print("None available. Record a WAV yourself and use --from-file.")
        return 0 if found else 1

    try:
        client = SpeakerClient.from_config(config)
    except SpeakerError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    try:
        status = client.status()
    except SpeakerError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if not status.get("supported", True):
        print("This Jarvis has no speaker verification, so there is nothing to enrol into.")
        return 1

    enrolled = int(status.get("samples") or 0)
    minimum = int(status.get("min_samples") or 3)
    print(f"Voice profile: {enrolled} sample(s) recorded, {minimum} needed.")
    if args.status:
        return 0

    # --- a file, if one was given --------------------------------------------
    if args.from_file:
        try:
            sample = read_wav(Path(args.from_file).read_bytes())
            result = client.enrol(sample)
        except (EnrolError, SpeakerError, OSError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
        print(f"Accepted. {result.get('samples', '?')} sample(s) now.")
        return 0

    # --- otherwise record ----------------------------------------------------
    recorder = find_recorder(args.recorder)
    if recorder is None:
        print(
            "No recorder found on this machine. Install one of "
            f"{', '.join(r.name for r in RECORDERS)}, or record a 16-bit WAV "
            "yourself and pass --from-file.",
            file=sys.stderr,
        )
        return 1

    prompts = [str(p) for p in (status.get("prompts") or []) if str(p).strip()]
    wanted = args.samples or max(1, minimum - enrolled)
    if not prompts:
        prompts = ["Say a sentence in your normal speaking voice."]
    print(f"Recording with {recorder.name}. {args.seconds}s per phrase, {wanted} to go.")

    accepted = 0
    for index in range(wanted):
        prompt = prompts[index % len(prompts)]
        print()
        print(f"  [{index + 1}/{wanted}]  {prompt}")
        try:
            input("  press enter, then read it aloud… ")
        except EOFError:
            # Piped stdin. Recording immediately is better than refusing, and
            # the countdown below is what makes that usable.
            print()
        try:
            raw = record_wav(recorder, args.seconds)
            sample = read_wav(raw)
            result = client.enrol(sample)
        except (EnrolError, SpeakerError) as err:
            # A refusal is jarvis-core's own sentence — "that sample has no
            # measurable pitch, it is too quiet" — and it is the actionable
            # part, so it is shown rather than replaced.
            print(f"  not accepted: {err}")
            continue
        accepted += 1
        print(f"  accepted ({result.get('samples', '?')} in the profile)")

    print()
    if accepted:
        print(f"Done. {accepted} sample(s) accepted.")
        return 0
    print("Nothing was accepted. Check the microphone and try again.", file=sys.stderr)
    return 1


SUBCOMMANDS = ("run", "tiers", "policy", "audit", "cron", "doctor", "status", "enrol")

#: Global flags that take a value, so the scanner below skips both tokens.
_GLOBAL_VALUE_FLAGS = ("-c", "--config", "--log-file")
_GLOBAL_BOOL_FLAGS = ("-v", "-vv", "-vvv", "--verbose", "-q", "--quiet")


def _default_to_run(argv: list[str]) -> list[str]:
    """``-m jarvis_desktop --server ...`` means ``run --server ...``.

    argparse has no notion of a default subcommand, and ``--server`` lives on
    the ``run`` subparser, so ``run`` is spliced in after the leading global
    flags when no subcommand was given. Flag *values* are skipped too, or
    ``-c config.json`` would look like a positional argument.
    """
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("-h", "--help", "--version"):
            return argv
        if token in _GLOBAL_VALUE_FLAGS:
            index += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in _GLOBAL_VALUE_FLAGS):
            index += 1
            continue
        if token in _GLOBAL_BOOL_FLAGS:
            index += 1
            continue
        break
    if index < len(argv) and argv[index] in SUBCOMMANDS:
        return argv
    return argv[:index] + ["run"] + argv[index:]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(_default_to_run(raw))
    _setup_logging(-1 if args.quiet else args.verbose, args.log_file)

    command = args.command or "run"
    try:
        if command == "cron":
            return cmd_cron(args)
        config = _config_from_args(args)
        if command == "run":
            return asyncio.run(cmd_run(config, once=getattr(args, "once", False)))
        if command == "tiers":
            return cmd_tiers(config)
        if command == "policy":
            return cmd_policy(config, args)
        if command == "audit":
            return cmd_audit(config, args)
        if command == "doctor":
            return cmd_doctor(config)
        if command == "status":
            return cmd_status(config, args)
        if command == "enrol":
            return cmd_enrol(config, args)
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
