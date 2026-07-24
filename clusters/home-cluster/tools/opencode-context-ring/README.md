# Local OpenCode context ring

This is installed as a global OpenCode TUI package at
`~/.config/opencode/plugins/local-context-ring`, registered from
`~/.config/opencode/tui.json`. It requires OpenCode 1.18.3 or newer, which
provides the TUI extension host.

It renders a live terminal-native Unicode context ring beside the composer and
in the session sidebar. The meter uses the token count recorded by OpenCode
for the latest assistant turn (input, output, reasoning, and cache tokens),
divided by that model's configured `limit.context` value. Local model entries
must therefore declare their real 32,768-token context limit in
`~/.config/opencode/opencode.json`.

The ring has no external calls, does not read transcript content, and keeps
the existing LiteLLM hard compaction guard as the enforcement layer.
