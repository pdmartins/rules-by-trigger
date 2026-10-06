---
name: doctor
description: >
  Setup, health-check, repair and removal for the rules-by-trigger plugin. Use
  when the user asks to set up, configure, verify, harden, troubleshoot,
  migrate or uninstall rules-by-trigger, or reports that a rule is not being
  injected.
---

# rules-by-trigger — doctor

One command runs every check; each finding names its fix.

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
"${CLAUDE_PLUGIN_ROOT}/bin/rules-by-trigger" doctor --root "$ROOT"
```

- A finding marked `[--fix applies it]` is applied by re-running with `--fix`.
- `setup: not done` — ask the language (options: the shipped translations
  plus "other") and whether to apply the recommended hardening, then run
  `doctor --setup --language <code> --harden|--no-harden`; if they decline
  the setup itself, `doctor --setup --decline` instead.
- A finding marked `[--harden applies it; ask the user first]` (inside or
  outside `--setup`) edits the user's own settings: ASK first, stating the
  trade-off in one line — every rule read and write then goes through this
  CLI (the manage skill does that anyway); it constrains the file tools, not
  subprocesses. Show what changed.
- A finding marked `[manual]` needs a decision only the user can make (a
  rule's type, an unreadable file). Bring it to them with the command named.
  A type is asked with options built from what `config --root "$ROOT"` prints,
  one question per untyped rule and up to four in a round — the mechanics and
  the fallbacks are in the manage skill's *Asking the user*.
- "Rule not firing?" — `status --root "$ROOT" --path '<file>'` runs the
  hook's own matcher on that path and says which rule covers it, or which
  filter took it back.
- Uninstall: `doctor --root "$ROOT" --uninstall` removes the deny entries and
  the cached state and lists the rule directories it keeps. Then the user runs
  `/plugin uninstall rules-by-trigger@pdmartins`. Never delete rule directories
  without asking: they are the user's authored content.
- After a first setup, offer the first rule: "ask me to add a rule for a
  folder, e.g. 'when touching src/api, always validate the DTOs'".
