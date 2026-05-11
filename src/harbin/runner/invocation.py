"""Agent CLI invocation modes (sub-spec 11)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harbin.config.models import AgentCli
from harbin.errors import RunnerError

PRESETS: dict[str, AgentCli] = {
    "copilot": AgentCli(command=["copilot"], mode="stdin"),
    "gh-copilot": AgentCli(
        command=["gh", "copilot", "suggest", "--target", "shell"],
        mode="stdin",
    ),
}


@dataclass(frozen=True)
class Invocation:
    """A fully-resolved subprocess invocation."""

    argv: list[str]
    stdin_text: str | None
    cleanup_path: Path | None  # tempfile to delete after run


def build(
    cli: AgentCli,
    prompt: str,
    *,
    prompts_dir: Path,
    short_id: str,
) -> Invocation:
    """Resolve an :class:`AgentCli` + prompt into an :class:`Invocation`."""
    mode = cli.mode
    if mode == "stdin":
        return Invocation(argv=list(cli.command), stdin_text=prompt, cleanup_path=None)

    placeholder = cli.placeholder
    if mode == "flag":
        if placeholder not in " ".join(cli.command):
            raise RunnerError(
                code="fleet.runner.invocation",
                message=f"placeholder '{placeholder}' not present in command",
            )
        argv = [arg.replace(placeholder, prompt) for arg in cli.command]
        return Invocation(argv=argv, stdin_text=None, cleanup_path=None)

    if mode == "tempfile":
        if placeholder not in " ".join(cli.command):
            raise RunnerError(
                code="fleet.runner.invocation",
                message=f"placeholder '{placeholder}' not present in command",
            )
        prompts_dir.mkdir(parents=True, exist_ok=True)
        tmp = prompts_dir / f"{short_id}.txt"
        tmp.write_text(prompt, encoding="utf-8")
        argv = [arg.replace(placeholder, str(tmp)) for arg in cli.command]
        return Invocation(argv=argv, stdin_text=None, cleanup_path=tmp)

    raise RunnerError(
        code="fleet.runner.invocation",
        message=f"unknown invocation mode: {mode!r}",
    )
