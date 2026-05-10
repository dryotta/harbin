# agent-cli-invocation — Sub-spec

> Status: **draft** · scope: the pluggability surface — how harbin hands a prompt to an agent CLI, allowed modes, default configs, per-fleet override shape, exit-code contract.
> Parent: [`design-overview.md`](./design-overview.md) §5.2.

The Agent Runner ([`10-agent-runner`](./10-agent-runner.md)) owns the **lifecycle** of a job. This doc owns the **shape of the command line and the prompt delivery**. The split exists because harbin is intentionally agent-agnostic: any CLI that follows reasonable conventions can be plugged in here without changing the runner.

---

## 1 · The `agent_cli` config block

This is the only configurable surface. Both `config.yaml` (global default) and `fleet.yaml` (per-fleet override) use the same schema:

```yaml
agent_cli:
  command: ["copilot"]               # required; argv prefix (binary + flags)
  mode: stdin                        # stdin | flag | tempfile
  placeholder: "${PROMPT}"           # required when mode == flag; ignored when mode == stdin
                                     # required when mode == tempfile (value substituted for the file path)
```

A `null` value in `fleet.yaml.agent_cli` means **inherit the global default**.

---

## 2 · Modes

Three delivery modes, one of which must match how the chosen CLI accepts a prompt.

### 2.1 `stdin` (default)

```yaml
agent_cli:
  command: ["copilot"]
  mode: stdin
```

Behavior:

- `argv = command`, unchanged.
- Subprocess stdin is a `PIPE`; the prompt text (UTF-8) is written and the pipe is closed.
- `${PROMPT}` placeholders in `command` are **rejected** as a config error — using both stdin and a placeholder is meaningless.

This is the recommended mode for CLIs that accept a prompt on stdin (the default for `gh copilot suggest`, many local LLM CLIs).

### 2.2 `flag`

```yaml
agent_cli:
  command: ["copilot", "--prompt", "${PROMPT}"]
  mode: flag
  placeholder: "${PROMPT}"
```

Behavior:

- The runner substitutes every occurrence of `placeholder` in `command` with the literal prompt text.
- `argv` is the substituted list. Subprocess stdin is `DEVNULL`.
- If `placeholder` is missing from `command`, harbin raises a config error at load.
- The placeholder may appear in any element (`["binary","--prompt=${PROMPT}"]` also works) because substitution is string-level.

Use when the CLI expects the prompt as a command-line argument and the prompt is short enough not to risk OS argv-length limits (`ARG_MAX` is typically 128–256 KiB).

### 2.3 `tempfile`

```yaml
agent_cli:
  command: ["my-agent", "--prompt-file", "${PROMPT_FILE}"]
  mode: tempfile
  placeholder: "${PROMPT_FILE}"
```

Behavior:

- The runner writes the prompt to a temp file under `paths.cache_dir / "prompts" / "<short-id>.txt"`, substitutes its absolute path for `placeholder` in `command`, and spawns. Subprocess stdin is `DEVNULL`.
- The temp file is **deleted** in the post-run hook (success, failure, or cancellation).
- This is the right choice for very long prompts (multi-KB system prompts) where flag-mode would hit `ARG_MAX`.

---

## 3 · Substitution discipline

- Substitution is **literal string replacement**, applied to each `argv` element before spawn.
- It is **not** shell substitution: no globbing, no variable expansion, no quoting concerns.
- Placeholder names beyond `${PROMPT}` and `${PROMPT_FILE}` are not interpreted; the user is free to choose another spelling as long as it appears in `command` and matches `placeholder`.
- A placeholder appearing more than once in `command` is substituted at every occurrence.

`HARBIN_*` env vars (see [`10-agent-runner`](./10-agent-runner.md) §2.1) are **not** substituted into `command`; they are exclusively available to the spawned process via the environment.

---

## 4 · Defaults for known CLIs

The defaults shipped in `harbin.runner.invocation.PRESETS` are reference values, not hard-coded behavior. They serve two purposes: (a) sensible out-of-the-box config for the common cases, (b) examples the `/config` modal can suggest.

| CLI | `command` | `mode` |
|---|---|---|
| GitHub Copilot CLI (Microsoft, agent mode) | `["copilot"]` | `stdin` |
| `gh copilot suggest` | `["gh", "copilot", "suggest", "--target", "shell"]` | `stdin` |
| (custom) any CLI that takes `--prompt` | `["my-cli", "--prompt", "${PROMPT}"]` | `flag` |

The global `config.yaml` default is the first row above. If `copilot` is not on PATH at startup, harbin still starts; jobs fail at spawn with a `RunnerError` whose message points at `/config → Agent runner`.

---

## 5 · Per-fleet override shape

In `fleet.yaml`:

```yaml
agent_cli:
  command: ["my-agent", "--mode", "harbin"]
  mode: stdin
```

Validation rules:

- A fleet override is either `null` (inherit) or a **complete** `AgentCli` block (no partial overrides). Partial overrides are explicitly not supported — too many subtle merge cases for a v1 feature.
- The block is validated by the same pydantic model as the global. Identical error surface (see [`03-configuration`](./03-configuration.md) §4).

Snapshot-at-spawn semantics ([`10-agent-runner`](./10-agent-runner.md) §8) apply: a running job uses whatever `agent_cli` was effective when it transitioned `queued → starting`.

---

## 6 · Exit-code contract

| Process exit | Job status | Rationale |
|---|---|---|
| 0 | `success` | Standard Unix convention |
| non-zero | `failed` | The agent signals failure however it wants; harbin trusts the exit code |
| SIGTERM / SIGKILL via `/cancel` | `cancelled` | See [`10-agent-runner`](./10-agent-runner.md) §5 |

There is **no** convention that "exit 2 means partial success" or similar. Agents that want richer status must emit it via their own artifacts (a `status.json`, a marker file). Harbin only sees the exit code.

---

## 7 · Examples

**GitHub Copilot CLI (default):**
```yaml
agent_cli:
  command: ["copilot"]
  mode: stdin
```

**`gh copilot` with a custom system prompt prefix:**
```yaml
agent_cli:
  command: ["gh", "copilot", "suggest", "-t", "shell"]
  mode: stdin
```

**A local script that takes a prompt as a flag:**
```yaml
agent_cli:
  command: ["./bin/agent.sh", "-p", "${PROMPT}"]
  mode: flag
  placeholder: "${PROMPT}"
```

**A heavyweight agent that needs a prompt file:**
```yaml
agent_cli:
  command: ["llm-cli", "run", "--config", "agent.yaml", "--prompt-file", "${PROMPT_FILE}"]
  mode: tempfile
  placeholder: "${PROMPT_FILE}"
```

---

## 8 · Open questions

- Whether to support an explicit `interpolate_env: bool = false` flag that would also substitute `${HARBIN_*}` into `command`. Not v1 — env-as-arg is a footgun with no current demand.
- Whether to support a *list of presets by name* (e.g. `agent_cli: { preset: copilot }`) so users don't repeat the command shape. Defer until two presets are actually shipped.

## 9 · Out of scope (v1)

- Streaming structured events back from the agent (JSON over stderr, etc.). The protocol is plain stdio.
- An MCP-like RPC surface between harbin and the agent.
- Per-job environment overrides beyond the standard `HARBIN_*` set.
