"""Tests for preflight.py -- first-run environment checks.

NO TEST IN THIS FILE MAY INVOKE THE REAL `hermes` BINARY, THE REAL
`osascript`, OR READ THE DEVELOPER'S REAL SETTINGS FILE.

Every subprocess call goes through the single seam `preflight._run_subprocess`
(the pattern `dialog.py` uses for `_osascript`), so tests monkeypatch that one
function rather than `asyncio.create_subprocess_exec` per call site. The
settings-file check goes through `preflight._settings_path`, which tests
point at a tmp_path fixture instead of the real home directory.
"""

import asyncio
import json

import pytest

import preflight
from preflight import Check, STATUS_FAIL, STATUS_OK, STATUS_WARN


def _fake_run_subprocess(responses):
    """Build a fake `_run_subprocess` keyed by (args tuple) -> (rc, stdout, stderr).

    Matching is by the first two positional args (e.g. ("hermes", "--version")
    or ("osascript", "-e")) so callers don't need to know exact remaining
    arguments (like the literal AppleScript source). Accepts (and ignores)
    an `env` kwarg.
    """
    async def fake(*args, timeout, env=None):
        key = tuple(args[:2])
        if key in responses:
            return responses[key]
        raise AssertionError(f"unexpected subprocess call: {args}")
    return fake


# --- accessibility -----------------------------------------------------------

@pytest.mark.asyncio
async def test_accessibility_granted(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(
        preflight, "_run_subprocess",
        _fake_run_subprocess({("osascript", "-e"): (0, "window 1\n", "")}),
    )
    check = await preflight._check_accessibility()
    assert check.status == STATUS_OK


@pytest.mark.asyncio
async def test_accessibility_not_granted(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/osascript")
    err = '65:69: execution error: System Events got an error: osascript is not allowed assistive access. (-1728)'
    monkeypatch.setattr(
        preflight, "_run_subprocess",
        _fake_run_subprocess({("osascript", "-e"): (1, "", err)}),
    )
    check = await preflight._check_accessibility()
    assert check.status == STATUS_FAIL
    assert check.remedy
    assert "Accessibility" in check.remedy


@pytest.mark.asyncio
async def test_accessibility_unknown_error_is_warn_not_ok(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(
        preflight, "_run_subprocess",
        _fake_run_subprocess({("osascript", "-e"): (1, "", "some other applescript error")}),
    )
    check = await preflight._check_accessibility()
    assert check.status == STATUS_WARN


@pytest.mark.asyncio
async def test_accessibility_skipped_off_darwin(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    check = await preflight._check_accessibility()
    assert check.status == STATUS_WARN


# --- Screen Recording ----------------------------------------------------------
#
# The same lesson as Accessibility, one permission along: macOS attributes it
# to the app that LAUNCHED JARVIS, not to python. And unlike Accessibility it
# fails silently -- `screencapture` exits 0 and hands back a black frame -- so
# it is worth saying at startup rather than at the moment he asks.

def test_screen_recording_granted(monkeypatch):
    monkeypatch.setattr(preflight.screen, "screen_recording_granted", lambda: True)
    check = preflight._check_screen_recording_sync()
    assert check.status == STATUS_OK
    assert check.remedy is None


def test_screen_recording_not_granted_is_fail_with_the_launching_app_remedy(monkeypatch):
    monkeypatch.setattr(preflight.screen, "screen_recording_granted", lambda: False)
    check = preflight._check_screen_recording_sync()
    assert check.status == STATUS_FAIL
    assert check.remedy
    assert "Screen Recording" in check.remedy
    assert "launched" in check.remedy.lower()


def test_screen_recording_undeterminable_is_warn_not_fail(monkeypatch):
    """None means the probe could not run -- off macOS, or a macOS that moved
    the symbol. Reporting that as a missing permission would send the user to
    a settings pane over nothing."""
    monkeypatch.setattr(preflight.screen, "screen_recording_granted", lambda: None)
    check = preflight._check_screen_recording_sync()
    assert check.status == STATUS_WARN


def test_screen_recording_check_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("CoreGraphics went sideways")

    monkeypatch.setattr(preflight.screen, "screen_recording_granted", boom)
    check = preflight._check_screen_recording_sync()
    assert check.status == STATUS_WARN


def test_the_startup_check_never_takes_a_picture(monkeypatch):
    """Preflight asks the OS a question. It does NOT capture the screen to
    find out -- that would be a screenshot the user never asked for, at every
    boot."""
    monkeypatch.setattr(preflight.screen, "screen_recording_granted", lambda: True)

    def forbidden(*a, **k):
        raise AssertionError("preflight captured the screen")

    monkeypatch.setattr(preflight.screen, "capture_screen", forbidden)
    assert preflight._check_screen_recording_sync().status == STATUS_OK


# --- Edge TTS -------------------------------------------------------------

def test_edge_tts_available_when_package_installed(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    check = preflight._check_edge_tts_connectivity_sync()
    assert check.status == STATUS_OK
    assert check.remedy is None


def test_edge_tts_missing_is_fail_with_install_remedy(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("No module named 'edge_tts'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    check = preflight._check_edge_tts_connectivity_sync()
    assert check.status == STATUS_FAIL
    assert check.remedy


# --- leftover ANTHROPIC_* ------------------------------------------------------

def test_leftover_anthropic_key_warns(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    check = preflight._check_anthropic_key_leftover_sync()
    assert check.status == STATUS_WARN
    assert "ANTHROPIC_API_KEY" in check.message
    assert check.remedy


def test_no_anthropic_key_is_ok(monkeypatch):
    for key in list(preflight.os.environ):
        if key.startswith("ANTHROPIC_"):
            monkeypatch.delenv(key, raising=False)
    check = preflight._check_anthropic_key_leftover_sync()
    assert check.status == STATUS_OK
    assert check.remedy is None


# --- crossSessionInbound --------------------------------------------------------

def test_cross_session_inbound_missing_settings_file(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "_settings_path", lambda: tmp_path / "does-not-exist.json")
    check = preflight._check_cross_session_inbound_sync()
    assert check.status == STATUS_WARN
    assert "No settings file" in check.message


def test_cross_session_inbound_malformed_json(monkeypatch, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ not valid json ][")
    monkeypatch.setattr(preflight, "_settings_path", lambda: p)
    check = preflight._check_cross_session_inbound_sync()
    assert check.status == STATUS_WARN
    assert "Could not read/parse" in check.message


def test_cross_session_inbound_absent_key(monkeypatch, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"model": "sonnet"}))
    monkeypatch.setattr(preflight, "_settings_path", lambda: p)
    check = preflight._check_cross_session_inbound_sync()
    assert check.status == STATUS_WARN
    assert "not set" in check.message
    # Read-only: the check must never have written to the file.
    assert json.loads(p.read_text()) == {"model": "sonnet"}


def test_cross_session_inbound_set_to_accept(monkeypatch, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"crossSessionInbound": "accept"}))
    monkeypatch.setattr(preflight, "_settings_path", lambda: p)
    check = preflight._check_cross_session_inbound_sync()
    assert check.status == STATUS_OK
    assert check.remedy is None


def test_cross_session_inbound_set_to_something_else(monkeypatch, tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"crossSessionInbound": "deny"}))
    monkeypatch.setattr(preflight, "_settings_path", lambda: p)
    check = preflight._check_cross_session_inbound_sync()
    assert check.status == STATUS_WARN
    assert "'deny'" in check.message or '"deny"' in check.message


def test_cross_session_inbound_never_writes_when_file_missing(monkeypatch, tmp_path):
    target = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(preflight, "_settings_path", lambda: target)
    preflight._check_cross_session_inbound_sync()
    assert not target.exists()


# --- run_checks(): timeouts, internal exceptions, concurrency -----------------

@pytest.mark.asyncio
async def test_a_hung_check_times_out_as_warn(monkeypatch):
    async def hang(*, timeout):
        await asyncio.sleep(999)
        return Check(name="hermes_cli", status=STATUS_OK, message="unreachable")

    monkeypatch.setattr(preflight, "_ASYNC_CHECKS", (hang,))
    monkeypatch.setattr(preflight, "_SYNC_CHECKS", ())
    monkeypatch.setattr(preflight, "_REACHABILITY_CHECKS", ())

    results = await preflight.run_checks(timeout=0.05)
    assert len(results) == 1
    assert results[0].status == STATUS_WARN
    assert "timed out" in results[0].message


@pytest.mark.asyncio
async def test_a_raising_check_becomes_warn_not_an_exception(monkeypatch):
    def explode():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(preflight, "_ASYNC_CHECKS", ())
    monkeypatch.setattr(preflight, "_SYNC_CHECKS", (explode,))
    monkeypatch.setattr(preflight, "_REACHABILITY_CHECKS", ())

    results = await preflight.run_checks(timeout=1.0)
    assert len(results) == 1
    assert results[0].status == STATUS_WARN
    assert "disk on fire" in results[0].message


@pytest.mark.asyncio
async def test_run_checks_never_raises_even_with_a_broken_check(monkeypatch):
    def explode():
        raise ValueError("boom")

    monkeypatch.setattr(preflight, "_SYNC_CHECKS", (explode,))
    # Should not raise.
    results = await preflight.run_checks(timeout=1.0)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_run_checks_runs_all_registered_checks(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)  # CLI missing -> fast fail
    monkeypatch.setattr(preflight.sys, "platform", "linux")  # accessibility skipped -> fast warn
    monkeypatch.setattr(preflight, "_settings_path", lambda: preflight.Path("/nonexistent/settings.json"))

    # Generous timeout: this asserts the SET of checks, and a slow first
    # import under load must not surface as a wrong name (a timed-out check
    # reports its function name, not its check name).
    results = await preflight.run_checks(timeout=10.0)
    names = {c.name for c in results}
    assert names == {
        "hermes_cli", "hermes_login", "accessibility", "screen_recording",
        "edge_tts", "anthropic_key_leftover", "cross_session_inbound",
        "edge_tts_reachability",
    }


# --- spoken_summary -------------------------------------------------------------

def test_spoken_summary_empty_when_all_ok():
    checks = [
        Check(name="hermes_cli", status=STATUS_OK, message="ok"),
        Check(name="edge_tts", status=STATUS_OK, message="ok"),
    ]
    assert preflight.spoken_summary(checks) == ""


def test_spoken_summary_never_says_all_checks_passed():
    checks = [Check(name="hermes_cli", status=STATUS_OK, message="ok")]
    summary = preflight.spoken_summary(checks)
    assert summary == ""
    assert "all checks passed" not in summary.lower()


def test_spoken_summary_names_a_single_failure():
    checks = [
        Check(name="hermes_cli", status=STATUS_OK, message="ok"),
        Check(name="hermes_login", status=STATUS_FAIL, message="`hermes usage` failed: Invalid API key"),
    ]
    summary = preflight.spoken_summary(checks)
    assert "One thing needs attention" in summary
    assert "isn't logged in" in summary
    assert "hermes_cli" not in summary  # only the failing check is named


def test_spoken_summary_says_screen_recording_in_words_a_person_would_use():
    checks = [Check(name="screen_recording", status=STATUS_FAIL,
                    message="JARVIS has not been granted Screen Recording.")]
    summary = preflight.spoken_summary(checks)
    assert "Screen Recording permission" in summary
    assert "screen_recording" not in summary


def test_spoken_summary_names_two_failures_and_matches_the_example():
    checks = [
        Check(name="hermes_login", status=STATUS_FAIL, message="`hermes usage` failed: Invalid API key"),
        Check(name="edge_tts", status=STATUS_FAIL, message="edge_tts is not installed."),
    ]
    summary = preflight.spoken_summary(checks)
    assert summary == (
        "Two things need attention, sir: the agent isn't logged in, "
        "and Edge TTS is not installed."
    )


def test_spoken_summary_names_only_the_failures_among_a_mix():
    checks = [
        Check(name="hermes_cli", status=STATUS_OK, message="ok"),
        Check(name="hermes_login", status=STATUS_OK, message="ok"),
        Check(name="accessibility", status=STATUS_FAIL,
              message="osascript is not granted Accessibility (assistive access); answer_dialog's keystroke will fail."),
        Check(name="cross_session_inbound", status=STATUS_WARN, message="crossSessionInbound is not set"),
        Check(name="anthropic_key_leftover", status=STATUS_WARN, message="ANTHROPIC_API_KEY set"),
    ]
    summary = preflight.spoken_summary(checks)
    assert summary.startswith("3 things need attention, sir:")
    assert "Accessibility permission" in summary
    assert "cross-session steering" in summary
    assert "leftover Anthropic API key" in summary


def test_spoken_summary_warn_counts_as_something_wrong():
    checks = [Check(name="cross_session_inbound", status=STATUS_WARN, message="crossSessionInbound is not set")]
    summary = preflight.spoken_summary(checks)
    assert summary != ""
    assert "One thing needs attention" in summary


# --- hermes backend: CLI + login -------------------------------------------

@pytest.mark.asyncio
async def test_hermes_cli_missing_from_path(monkeypatch):
    monkeypatch.setenv("JARVIS_CLI_PATH", "hermes")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    check = await preflight._check_hermes_cli()
    assert check.status == STATUS_FAIL
    assert "PATH" in check.message


@pytest.mark.asyncio
async def test_hermes_cli_present_is_ok(monkeypatch):
    monkeypatch.setenv("JARVIS_CLI_PATH", "hermes")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/local/bin/hermes")
    monkeypatch.setattr(
        preflight, "_run_subprocess",
        _fake_run_subprocess({("/usr/local/bin/hermes", "--version"): (0, "hermes 1.2.3\n", "")}),
    )
    check = await preflight._check_hermes_cli()
    assert check.status == STATUS_OK


@pytest.mark.asyncio
async def test_hermes_login_failure_names_setup(monkeypatch):
    monkeypatch.setenv("JARVIS_CLI_PATH", "hermes")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/local/bin/hermes")
    monkeypatch.setattr(
        preflight, "_run_subprocess",
        _fake_run_subprocess({("/usr/local/bin/hermes", "usage"): (1, "", "Invalid API key")}),
    )
    check = await preflight._check_hermes_login()
    assert check.status == STATUS_FAIL
    assert "hermes setup" in check.remedy


@pytest.mark.asyncio
async def test_hermes_backend_selects_hermes_checks(monkeypatch):
    monkeypatch.setenv("JARVIS_CLI_PATH", "hermes")
    assert [fn.__name__ for fn in preflight._selected_async_checks()] == [
        "_check_hermes_cli", "_check_hermes_login", "_check_accessibility"]
