---
name: pr
description: Open a pull request for the current branch with .github/pull_request_template.md filled in from the real diff and the suites that were actually run. User-triggered only.
disable-model-invocation: true
---

# /pr — open the pull request

Arguments: `$ARGUMENTS` — optional base branch. Default `claude/jarvis-ai-assistant-nbqf1p`, this checkout's integration branch (`dev` is the upstream model the template describes).

Needs the GitHub CLI, authenticated: `gh auth status`. It lives in `~/.local/bin/gh`; if a bare `gh` is not found, use that path (`~/.profile` adds the directory to `PATH` on the next login). Remote: `git@github.com:ChaseSunstrom/jarvis.git`.

## 1. Preconditions

- Working tree clean; branch pushed (`git push -u origin HEAD` if not).
- Know what was run on this exact HEAD. If `/gate` has not been run since the last commit, run it first — the template's checkboxes are ticked only for suites that actually ran here.

## 2. Title

Sentence-style, no conventional-commit prefix, the behaviour change from the user's point of view, ≤ 72 characters — the same register as this repo's commit subjects (`Close three ways a coding job could reach the host`).

## 3. Body — the template, section by section, no placeholders left

- **What changed, and why** — the behaviour, not the diff: what was wrong or missing before.
- **How it was checked** — tick `make test` / `make test-web` only if they ran; name what could not be run here (hardware, Docker daemon, Playwright browsers) and why.
- **Anything a reviewer should look at twice** — a security boundary, a wire contract another client parses (console, Android, desktop), a default that changes for existing installs, a claim only real hardware can settle. If none, say so.
- **Docs** — whether `docs/verification.md` / `DEVIATIONS.md` moved; run `/claims` if unsure.

End the body with `🤖 Generated with [Claude Code](https://claude.com/claude-code)` and the session link if there is one.

## 4. Create

```bash
gh pr create --base "<base>" --head "$(git branch --show-current)" --title "<title>" --body-file <file>
```

Print the URL.
