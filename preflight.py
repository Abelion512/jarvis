"""
JARVIS preflight -- first-run environment checks.

Every one of these has already bitten this project: the agent CLI missing or
too old, not logged in (the voice brain runs on the user's *subscription*,
never an API key -- see brain.py's SCRUBBED_ENV_PREFIXES),
`crossSessionInbound` not accepting steers into other sessions, `osascript`
lacking Accessibility so `answer_dialog`'s keystroke fails, and no TTS
engine at all.

This module only *observes*. Nothing here writes a file, changes a setting,
or grants a permission -- see `_check_cross_session_inbound_sync`'s
docstring for why that one in particular must stay read-only. It runs at
server startup, so the contract is the same one `notifier.py` keeps for its
own subprocess boundary: **never raise**. A check that itself errors, hangs,
or can't be parsed becomes a `warn` Check carrying the error text, never an
exception -- this must not be able to prevent the server from booting.

Subprocess handling follows notifier.py's pattern (read it first): spawn off
the event loop, bound by a timeout, kill-and-reap on timeout, decode leniently.
All process boundaries funnel through `_run_subprocess` below so tests can
mock the one seam instead of patching `asyncio.create_subprocess_exec`
per-call (the pattern `dialog.py` uses for `_osascript`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import screen

log = logging.getLogger("jarvis.preflight")

# A hung subprocess (a wedged agent CLI, a modal `osascript` is waiting on)
# must not stall server startup. Each check gets its own budget; they run
# concurrently in run_checks() so the wall-clock cost is one timeout, not
# the sum of them.
DEFAULT_CHECK_TIMEOUT = 5.0

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"


@dataclass(frozen=True)
class Check:
    """One preflight result.

    `remedy` is a concrete, user-actionable next step ("run `claude` and
    log in") -- it is None exactly when `status` is "ok", since a passing
    check has nothing to remedy.
    """
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    remedy: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


# ── the one subprocess boundary ─────────────────────────────────────────

async def _run_subprocess(*args: str, timeout: float,
                           env: Optional[dict[str, str]] = None) -> tuple[int, str, str]:
    """Run a subprocess, capturing stdout/stderr, bounded by `timeout`.

    `env=None` (the default) inherits this process's ambient environment,
    same as before. A caller that needs the subprocess to see a SPECIFIC
    environment -- e.g. `hermes_login` checking under exactly the
    environment the brain spawns with -- passes one explicitly.

    Never raises: a spawn failure or a timeout comes back as returncode -1
    with the problem described in stderr, exactly like notifier.py's
    `notify()` treats a wedged or missing `osascript`. This is the single
    seam every check in this module spawns a process through, so tests can
    mock it once instead of patching `asyncio.create_subprocess_exec` at
    each call site.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as e:
        return -1, "", f"failed to spawn {args[0] if args else '?'}: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.communicate()
        except Exception:
            pass
        return -1, "", f"{args[0] if args else '?'} timed out after {timeout}s"

    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


# ── which agent CLI ────────────────────────────────────────────────────
# Hermes-only: JARVIS_CLI_PATH names the binary (default `hermes`).
# The legacy JARVIS_CLAUDE_PATH is still honoured as a path override.

def _cli_backend() -> str:
    return "hermes"


def _cli_binary() -> str:
    raw = os.getenv("JARVIS_CLI_PATH") or os.getenv("JARVIS_CLAUDE_PATH")
    if raw:
        return raw.split()[0]
    return "hermes"


async def _check_hermes_cli(timeout: float = DEFAULT_CHECK_TIMEOUT) -> Check:
    """`hermes` is on PATH. No minimum version: no JARVIS feature depends
    on a version-gated Hermes flag."""
    binary = _cli_binary()
    resolved = shutil.which(binary) or (binary if Path(binary).exists() else None)
    if not resolved:
        return Check(
            name="hermes_cli",
            status=STATUS_FAIL,
            message="`hermes` is not on PATH.",
            remedy="Install Hermes Agent and make sure it's on PATH.",
        )
    rc, stdout, stderr = await _run_subprocess(resolved, "--version", timeout=timeout)
    if rc != 0:
        return Check(
            name="hermes_cli",
            status=STATUS_WARN,
            message=f"`hermes --version` failed: {(stderr or stdout).strip() or f'exit {rc}'}",
            remedy="Run `hermes --version` yourself to see what's wrong.",
        )
    version = (stdout or stderr).strip().splitlines()
    detail = version[0][:80] if version else "on PATH"
    return Check(name="hermes_cli", status=STATUS_OK, message=f"hermes {detail}.")


async def _check_hermes_login(timeout: float = DEFAULT_CHECK_TIMEOUT) -> Check:
    """The Hermes provider answers without starting a session.

    `hermes usage` prints the account's rate-limit windows without opening
    a session — the cheapest authenticated call it has. A non-zero exit
    means the provider rejected the credentials.
    """
    binary = _cli_binary()
    resolved = shutil.which(binary) or (binary if Path(binary).exists() else None)
    if not resolved:
        return Check(
            name="hermes_login",
            status=STATUS_WARN,
            message="Can't check Hermes login: `hermes` is not on PATH.",
            remedy="Install Hermes Agent and configure a provider with `hermes setup`.",
        )
    rc, stdout, stderr = await _run_subprocess(resolved, "usage", timeout=timeout)
    if rc != 0:
        text = (stderr or stdout).strip()[:200]
        # Custom/OpenAI-compatible endpoints (e.g. a local proxy) have no
        # usage endpoint at all — `hermes usage` exits 1 saying exactly
        # that, while real turns authenticate fine. The brain's warm-up
        # turn is the true auth check for these; failing here would nag on
        # every boot over a probe that cannot succeed by design.
        if "no usage endpoint" in text.lower():
            return Check(name="hermes_login", status=STATUS_OK,
                         message="Hermes provider reachable (no usage endpoint on custom providers).")
        return Check(
            name="hermes_login",
            status=STATUS_FAIL,
            message=f"`hermes usage` failed: {text or f'exit {rc}'}",
            remedy="Run `hermes setup` in a terminal and configure a provider.",
        )
    return Check(name="hermes_login", status=STATUS_OK,
                 message="Hermes provider answered.")


# macOS's own wording (and error code) for "Accessibility not granted" --
# distinctive enough it can't match an ordinary AppleScript error. Mirrors
# dialog.py's _PERMISSION_MARKERS, which sees the sibling "-1743"/"-25211"
# errors for a different System Events call.
_ACCESSIBILITY_MARKERS = (
    "-1728",
    "not allowed assistive access",
)


async def _check_accessibility(timeout: float = DEFAULT_CHECK_TIMEOUT) -> Check:
    """Whether osascript has Accessibility (assistive access), without prompting for it.

    `answer_dialog` sends a keystroke via System Events; without this
    permission the first real keypress fails. Asking System Events for a
    window list is read-only and, verified live on the dev machine, does
    NOT trigger a permission dialog when access is missing -- it simply
    returns AppleScript error -1728. If that ever stops being true on some
    macOS version, this comes back as a WARN (unrecognised error) rather
    than mis-reporting OK, so it fails safe.
    """
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return Check(
            name="accessibility",
            status=STATUS_WARN,
            message="Cannot check Accessibility: not macOS, or osascript is missing.",
        )

    rc, stdout, stderr = await _run_subprocess(
        "osascript", "-e",
        'tell application "System Events" to tell process "Finder" to get name of every window',
        timeout=timeout,
    )

    if rc == 0:
        return Check(name="accessibility", status=STATUS_OK, message="osascript has Accessibility access.")

    combined = f"{stdout}\n{stderr}".lower()
    if any(marker in combined for marker in _ACCESSIBILITY_MARKERS):
        return Check(
            name="accessibility",
            status=STATUS_FAIL,
            message="osascript is not granted Accessibility (assistive access); answer_dialog's keystroke will fail.",
            remedy=(
                "macOS attributes this to the app that launched JARVIS, not to "
                "python or osascript. Grant that app under System Settings -> "
                "Privacy & Security -> Accessibility, or start the server from a "
                "terminal that already has it. If the app is already ticked and "
                "this still fails, check whether it is running from "
                "/private/var/.../AppTranslocation/ (`ps -o comm= -p <its pid>`): "
                "macOS runs apps opened straight from Downloads at a randomised "
                "path, and a grant does not follow them there. Move the app to "
                "/Applications and relaunch it."
            ),
        )

    return Check(
        name="accessibility",
        status=STATUS_WARN,
        message=f"Could not determine Accessibility status: {(stderr or stdout).strip()}",
    )


def _check_screen_recording_sync() -> Check:
    """Whether JARVIS may see the screen at all -- asked, never demonstrated.

    The same lesson as Accessibility one permission along, and worse in one
    respect: Accessibility fails loudly (AppleScript error -1728), while a
    `screencapture` without Screen Recording exits 0 and hands back a black
    or desktop-only frame. `screen.capture_screen` refuses such a frame at
    the moment of asking; this says it at startup, before the user has spoken.

    It asks CoreGraphics (`CGPreflightScreenCaptureAccess`, the non-prompting
    one) and NEVER captures anything to find out -- a screenshot the user did
    not ask for, at every boot, is precisely what this capability must not do.
    """
    try:
        granted = screen.screen_recording_granted()
    except Exception as e:  # the module must never take startup down
        return Check(name="screen_recording", status=STATUS_WARN,
                     message=f"Could not determine Screen Recording status: {e}")

    if granted is True:
        return Check(name="screen_recording", status=STATUS_OK,
                     message="JARVIS has Screen Recording access.")
    if granted is None:
        return Check(
            name="screen_recording", status=STATUS_WARN,
            message=("Could not determine Screen Recording status: not macOS, "
                     "or CoreGraphics could not be asked."))
    return Check(
        name="screen_recording",
        status=STATUS_FAIL,
        message="JARVIS has not been granted Screen Recording; look_at_screen will refuse.",
        remedy=(
            "macOS attributes this to the app that launched JARVIS, not to "
            "python or screencapture -- the same rule as Accessibility above. "
            "Grant that app Screen Recording under System Settings -> Privacy "
            "& Security -> Screen & System Audio Recording, then RESTART it: "
            "the grant only reaches a process started after it was given."
        ),
    )


def _check_edge_tts_connectivity_sync() -> Check:
    """Edge TTS requires no API key, but the edge_tts package must be installed."""
    try:
        import edge_tts  # noqa: F401
        return Check(name="edge_tts", status=STATUS_OK,
                     message="Edge TTS is available (no API key required).")
    except ImportError:
        return Check(
            name="edge_tts",
            status=STATUS_FAIL,
            message="edge_tts is not installed.",
            remedy="Run: pip install edge-tts",
        )


EDGE_TTS_HOST = "speech.platform.bing.com"
EDGE_TTS_PORT = 443


async def _probe_edge_tts_endpoint(host: str = EDGE_TTS_HOST,
                                   port: int = EDGE_TTS_PORT,
                                   timeout: float = 3.0) -> bool:
    """TCP reachability to the Edge TTS endpoint. True when connectable."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout)
        try:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        finally:
            pass
        return True
    except Exception:
        return False


async def _check_edge_tts_reachability(timeout: float = 3.0) -> Check:
    """Warn-only: offline box warns at boot instead of failing first utterance."""
    try:
        ok = await _probe_edge_tts_endpoint(timeout=timeout)
    except Exception as e:  # never fail boot on a probe
        return Check(name="edge_tts_reachability", status=STATUS_WARN,
                     message=f"Edge TTS reachability unknown: {e}")
    if ok:
        return Check(name="edge_tts_reachability", status=STATUS_OK,
                     message="Edge TTS endpoint reachable.")
    return Check(
        name="edge_tts_reachability", status=STATUS_WARN,
        message="Edge TTS endpoint unreachable — voice will fail until network returns.",
        remedy="Check network/VPN; JARVIS boots anyway and retries at first utterance.",
    )


def _check_anthropic_key_leftover_sync() -> Check:
    """A leftover ANTHROPIC_* var signals a misconfigured .env.

    brain.py already scrubs every ANTHROPIC_* variable from the brain's
    child process (see SCRUBBED_ENV_PREFIXES), so this can no longer make
    the voice path silently bill an API key. It is still worth reporting:
    its presence means someone put an Anthropic API key in `.env`, which is
    not how this project authenticates (see brain-subscription-only-env-scrub
    history) and is a sign the rest of the setup may be off too.
    """
    leftover = sorted(k for k in os.environ if k.startswith("ANTHROPIC_"))
    if not leftover:
        return Check(
            name="anthropic_key_leftover",
            status=STATUS_OK,
            message="No leftover ANTHROPIC_* variables in the environment.",
        )
    return Check(
        name="anthropic_key_leftover",
        status=STATUS_WARN,
        message=(
            f"{', '.join(leftover)} set in the environment. brain.py scrubs these "
            "from the brain's child process, but this signals a misconfigured .env."
        ),
        remedy=(
            "Remove ANTHROPIC_* variables from .env -- JARVIS's voice brain runs "
            "on your Hermes provider login, not an API key."
        ),
    )


def _settings_path() -> Path:
    """Where the CLI's user settings.json lives.

    Honours CLAUDE_CONFIG_DIR (the env var the CLI itself, and session_watch.py,
    respect) so this reports on the file `claude` will actually read for this
    process; falls back to the CLI's default of ~/.claude.
    """
    root = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return Path(root).expanduser() / "settings.json"


def _check_cross_session_inbound_sync() -> Check:
    """Report -- never change -- what settings.json says about crossSessionInbound.

    Steering a session posts to its inbox socket; if the receiving side is
    not configured to accept, steers are held for approval or dropped. The
    fix is `"crossSessionInbound": "accept"` in the user's settings.json,
    but this function must never write it: the design is that JARVIS offers
    and the user agrees, never a silent write. This function only reads.
    """
    path = _settings_path()
    if not path.exists():
        return Check(
            name="cross_session_inbound",
            status=STATUS_WARN,
            message=(
                f"No settings file at {path}; crossSessionInbound is unset, so "
                "steers into other sessions will be held for approval or dropped."
            ),
            remedy=(
                'Offer to add "crossSessionInbound": "accept" to that settings.json '
                "-- only if the user agrees."
            ),
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        return Check(
            name="cross_session_inbound",
            status=STATUS_WARN,
            message=f"Could not read/parse {path}: {e}",
            remedy=f"Check that {path} is valid JSON.",
        )

    if not isinstance(data, dict):
        return Check(
            name="cross_session_inbound",
            status=STATUS_WARN,
            message=f"{path} did not contain a JSON object.",
            remedy=f"Check that {path} is valid JSON.",
        )

    value = data.get("crossSessionInbound")
    if value == "accept":
        return Check(
            name="cross_session_inbound",
            status=STATUS_OK,
            message=f'crossSessionInbound is "accept" in {path}.',
        )

    if value is None:
        detail = f"crossSessionInbound is not set in {path}"
    else:
        detail = f'crossSessionInbound is {value!r} in {path}, not "accept"'

    return Check(
        name="cross_session_inbound",
        status=STATUS_WARN,
        message=f"{detail}; steers into other sessions will be held for approval or dropped.",
        remedy=(
            'Offer to set "crossSessionInbound": "accept" in that settings.json '
            "-- only if the user agrees; never write it silently."
        ),
    )


def _read_settings(path: Path) -> dict | None:
    """The settings object, or None if it is missing or not readable JSON.

    None is deliberately NOT the same as `{}`: a caller about to write must
    be able to tell "there is no file" from "there is a file I could not
    parse", because overwriting the second would destroy the user's hooks,
    plugins, marketplaces and status line.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def cross_session_inbound_accepted() -> bool:
    """True only when settings.json says `"crossSessionInbound": "accept"`.

    Cheap enough to call on the steer path. Anything else — no file, bad
    JSON, a different value — is False, because in every one of those cases
    the message really will be held for the user to approve.
    """
    path = _settings_path()
    if not path.exists():
        return False
    data = _read_settings(path)
    return bool(data) and data.get("crossSessionInbound") == "accept"


def enable_cross_session_inbound() -> tuple[bool, str]:
    """Write `"crossSessionInbound": "accept"`, preserving everything else.

    Called ONLY after the user has said yes out loud — the design has always
    been that JARVIS offers and the user agrees, and nothing here should ever
    be reached from a preflight check or a background turn.

    Read-modify-write on the parsed object: this user's settings.json holds
    hooks, plugins, marketplaces and a status line, and every one of them
    survives. A file that exists but does not parse is REFUSED rather than
    replaced — a broken JSON file is still the user's configuration, and
    guessing at it would lose the lot.
    """
    path = _settings_path()
    if path.exists():
        data = _read_settings(path)
        if data is None:
            return False, (f"{path} isn't readable JSON; I won't rewrite it.")
    else:
        data = {}
    if data.get("crossSessionInbound") == "accept":
        return True, "already set"

    data["crossSessionInbound"] = "accept"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written whole, then moved into place, so an interrupted write can
        # never leave a truncated settings.json behind.
        tmp = path.with_name(path.name + ".jarvis-tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        return False, f"could not write {path}: {e}"
    return True, str(path)


# ── running them all ─────────────────────────────────────────────────────

_ASYNC_CHECKS = (_check_hermes_cli, _check_hermes_login, _check_accessibility)
_SYNC_CHECKS = (_check_edge_tts_connectivity_sync, _check_anthropic_key_leftover_sync,
                _check_cross_session_inbound_sync, _check_screen_recording_sync)
_REACHABILITY_CHECKS = (_check_edge_tts_reachability,)


def _selected_async_checks() -> tuple:
    """The agent-CLI checks plus the platform checks that never vary."""
    return _ASYNC_CHECKS


async def _run_one(fn, *, is_async: bool, timeout: float) -> Check:
    """Run one check, bounded by `timeout`, and never let it raise or hang.

    A check that errors internally, or simply runs long (a wedged
    subprocess, an unexpectedly slow disk), becomes a `warn` Check instead
    of propagating -- this runs at server startup and must not be able to
    block or crash it.
    """
    name = getattr(fn, "__name__", "check")
    try:
        if is_async:
            coro = fn(timeout=timeout)
        else:
            coro = asyncio.to_thread(fn)
        # A little slack over the inner subprocess timeout so a check that
        # honours its own `timeout` argument reports its own message
        # instead of being pre-empted by this outer guard.
        return await asyncio.wait_for(coro, timeout=timeout + 1.0)
    except asyncio.TimeoutError:
        return Check(name=name, status=STATUS_WARN, message=f"Check '{name}' timed out.")
    except Exception as e:  # belt and suspenders: this must never raise into the caller
        log.warning(f"preflight: check '{name}' raised: {e}")
        return Check(name=name, status=STATUS_WARN, message=f"Check '{name}' raised: {e}")


async def run_checks(*, timeout: float | None = None) -> list[Check]:
    """Run every environment check concurrently, each individually time-boxed.

    Never raises. Safe to call at startup: the worst case is a handful of
    `warn` results after `timeout` seconds, not a hung or crashed server.

    Hermes Agent spends ~15-30s on plugin/MCP init before answering
    anything, so the budget defaults to 30s — a 5s budget would false-fail
    every check at boot.
    """
    if timeout is None:
        timeout = 30.0
    tasks = [_run_one(fn, is_async=True, timeout=timeout) for fn in _selected_async_checks()]
    tasks += [_run_one(fn, is_async=False, timeout=timeout) for fn in _SYNC_CHECKS]
    tasks += [_run_one(fn, is_async=True, timeout=3.0) for fn in _REACHABILITY_CHECKS]
    return await asyncio.gather(*tasks)


# ── spoken summary ───────────────────────────────────────────────────────

# Short, voice-friendly phrases keyed by check name. Deliberately not the
# full `message` text (which is written for logs/UI, not a sentence spoken
# aloud) -- picked by a substring of the message so the phrase still fits
# the specific failure (e.g. "isn't installed" vs. "needs updating").
def _phrase_for(check: Check) -> str:
    name, msg = check.name, check.message
    if name == "hermes_cli":
        if "not on PATH" in msg:
            return "the agent CLI isn't installed"
        return "the agent CLI's version couldn't be checked"
    if name == "hermes_login":
        if "not logged in" in msg or "failed" in msg:
            return "the agent isn't logged in"
        return "the agent's login couldn't be checked"
    if name == "accessibility":
        if "not granted Accessibility" in msg:
            return "I don't have Accessibility permission"
        return "Accessibility couldn't be checked"
    if name == "screen_recording":
        if "not been granted" in msg:
            return "I don't have Screen Recording permission"
        return "Screen Recording couldn't be checked"
    if name == "edge_tts":
        return "Edge TTS is not installed"
    if name == "edge_tts_reachability":
        return "Edge TTS is unreachable from here"
    if name == "anthropic_key_leftover":
        return "there's a leftover Anthropic API key in the environment"
    if name == "cross_session_inbound":
        return "cross-session steering isn't enabled"
    return check.message


def spoken_summary(checks: list["Check"]) -> str:
    """One or two spoken sentences naming what's wrong, or "" when all is well.

    Silence is the correct report for a healthy system: this deliberately
    never produces "all checks passed" -- only what needs attention, so the
    voice path has nothing to say when there is nothing to say.
    """
    issues = [c for c in checks if c.status != STATUS_OK]
    if not issues:
        return ""

    phrases = [_phrase_for(c) for c in issues]

    if len(phrases) == 1:
        return f"One thing needs attention, sir: {phrases[0]}."
    if len(phrases) == 2:
        return f"Two things need attention, sir: {phrases[0]}, and {phrases[1]}."
    return (
        f"{len(phrases)} things need attention, sir: "
        + ", ".join(phrases[:-1])
        + f", and {phrases[-1]}."
    )
