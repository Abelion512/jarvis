# Contributing to JARVIS

Thanks for your interest — JARVIS is a small, personal voice AI assistant and
the contribution bar is deliberately low. This guide adapts patterns from the
[hermes-agent](https://github.com/NousResearch/hermes-agent) project's
`CONTRIBUTING.md`.

---

## Contribution Priorities

We value contributions, roughly in this order:

1. **Bug fixes** — crashes, broken voice, silent failures, data loss
2. **Platform compatibility** — macOS (primary), plus Windows and WSL2 where feasible
3. **Error handling & robustness** — retry logic, timeout handling, graceful degradation
4. **Skills** — narrowly focused, broadly useful voice integrations
5. **Frontend** — dashboard polish, new visualization panels
6. **Documentation** — fixes, setup guides, troubleshooting

---

## Before You Start: Search First

- Search [open and closed issues](https://github.com/abelion/jarvis/issues) to avoid duplicates
- Search [open and merged PRs](https://github.com/abelion/jarvis/pulls?q=is%3Apr%20is%3Aall) for your topic
- The issue tracker may lag the code — search the source before proposing something already implemented
- For larger work, comment on an issue first to signal intent and avoid overlap

---

## What We're Looking For

- **Bug fixes** — if something's broken, fix it
- **New integrations** — Spotify, Slack, Notion, etc.
- **Cross-platform support** — the AppleScript integrations are macOS-only; cross-platform alternatives are welcome
- **Better error handling** — things fail silently in places
- **Voice improvements** — alternative TTS providers, better speech recognition
- **New actions** — extend what JARVIS can do

## What NOT to Do

- Don't add telemetry or analytics
- Don't send data to external services beyond the existing API calls (Fish Audio)
- Don't add features that modify or delete user data in the services somebody has connected
- Don't break the existing voice loop

---

## Development Setup

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| **macOS 13+** | JARVIS is primarily a macOS voice assistant (AppleScript, VoiceOver) |
| **Python 3.11+** | The code uses `X | None` at runtime |
| **Node.js 22+** | For the frontend dashboard build |
| **Chrome** | Microphone access requires Chrome (not Safari/Firefox) |
| **Claude Code** | `npm install -g @anthropic-ai/claude-code` and log in with `claude` |

### Quick Start

1. Fork and clone your fork
2. Copy `.env.example` to `.env` and fill in API keys
3. Install Python deps: `pip install -r requirements.txt`
4. Install Playwright: `python -m playwright install chromium`
5. Install frontend deps: `cd frontend && bun install`
6. Generate SSL certs (required for local HTTPS):
   ```bash
   openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
   ```
7. Start the backend: `python server.py --host 127.0.0.1`
8. Start the frontend: `cd frontend && bun run dev`
9. Open Chrome to `http://localhost:5173` and enable audio

### Running Tests

```bash
pytest -v -o faulthandler_timeout=300 -p no:cacheprovider
```

- Set `JARVIS_BRAIN_AUTOSTART=0` to run tests without starting the brain
- Browser-integration tests are deselected by `pytest.ini` — they run only on CI
- All other tests run offline (no Fish Audio key, no Claude login needed)

---

## Project Structure

```
jarvis/
├── server.py          # FastAPI app — the main backend (~5,600 lines)
├── brain.py           # Conversation loop, tool dispatch, system prompt
├── jarvis_mcp.py      # MCP server: exposes JARVIS actions as tools
├── jarvis_memory.py   # Memory management (MEMORY.md, USER.md persistence)
├── run_store.py       # Records every agent execution as a "run" (watched at /dashboard)
├── run_executor.py    # Subprocess management for Claude Code / Hermes runs
├── data_paths.py      # Platform-aware path resolution (macOS/Windows/venv)
├── notifier.py        # macOS notifications via AppleScript
├── speech.py          # Voice input (listening) and output (TTS via Edge TTS)
├── screen.py          # Screenshots and screen analysis (macOS)
├── session_watch.py   # WebSocket session management
├── mcp.json           # MCP server configuration
├── migrations/        # SQL migration files
├── frontend/          # Vue + Vite dashboard
├── skills/            # JARVIS skills (including jarvis-setup)
└── tests/             # Test suite
```

---

## Code Style

- **PEP 8** with practical exceptions (no strict line length enforcement)
- **Comments**: only explain non-obvious intent, trade-offs, or gotchas — don't narrate what the code does
- **Error handling**: catch specific exceptions; log clearly with `logger.warning()` or `logger.error()`
- **Error messages**: name the actual cause and the remediation step, never just the symptom
- **Keep voice responses short** (1-2 sentences max)
- **Don't add dependencies** unless absolutely necessary — prefer stdlib Python, curl, and existing tools
- **Personality**: British butler, dry wit, economy of language

### Note on `server.py`

Yes, `server.py` is a large monolith. It works. If you want to refactor parts into modules, that's welcome — just make sure nothing breaks.

---

## Pull Request Process

### Branch naming

```
fix/description        # Bug fixes
feat/description       # New features
docs/description       # Documentation
test/description       # Tests
refactor/description   # Code restructuring
chore/description      # Build, CI, deps
```

### Before submitting

1. Run tests: `pytest -v -o faulthandler_timeout=300 -p no:cacheprovider`
2. If you touched TypeScript: `cd frontend && npx tsc --noEmit`
3. Test manually: start the server, talk to JARVIS, confirm it works
4. Check cross-platform impact if you touched file paths or OS-specific code
5. Keep PRs focused: one logical change per PR

### PR description

Include:
- What changed and why
- How to test it
- What platforms you tested on
- Reference any related issues

### Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

| Type | Use for |
|------|---------|
| `fix` | Bug fixes |
| `feat` | New features |
| `docs` | Documentation |
| `test` | Tests |
| `refactor` | Code restructuring |
| `chore` | Build, CI, deps |

---

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Your OS and Python version
- Any error messages from the terminal
- Steps to reproduce

For security vulnerabilities, report them privately via GitHub's security advisory.

---

## Questions?

Open an issue, start a discussion, or reach out. Keep it simple.

---

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
