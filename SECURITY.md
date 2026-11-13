# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue. Open the repository's
[Security tab](https://github.com/lucas-saldanha-werneck/save-clear/security) and choose
"Report a vulnerability" (GitHub private vulnerability reporting).

## Supported versions

Only the latest release is supported. Fixes land there.

## What this plugin accesses

- Files: it writes the session summary to `~/.claude/snapshots`, keeps its state under `~/.claude/state/save-clear`, and reads your options from `~/.config/save-clear/config.json`. The optional idle trigger reads session transcripts under `~/.claude/projects`.
- Keystrokes: to run `/clear` it types into your own session's tmux pane or iTerm2 tab, after checking that the target runs that session.
- Desktop app: the optional relay runs a local helper session and opens `claude://resume` links.
- Commands: `on_save_command`, `pre_clear_command` and `on_resume_command` run only if you set them in the config, with your own permissions.
- Network: the plugin's own code makes no network requests, and it does not read secrets.

