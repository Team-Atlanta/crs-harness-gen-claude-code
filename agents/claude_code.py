"""
Claude Code agent for autonomous harness generation.

Implements the agent interface (setup / run) using Claude Code CLI
in agentic mode. Claude Code reads CLAUDE.md for workflow instructions,
then autonomously: downloads fuzz-proj and target source -> explores APIs ->
writes new harness(es) -> builds via libCRS -> iterates ->
copies final fuzz-proj to harness_dir.
"""

import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("agent.claude_code")


# 0 = no timeout (run until budget is exhausted)
try:
    AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "0"))
except ValueError:
    AGENT_TIMEOUT = 0
if AGENT_TIMEOUT < 0:
    AGENT_TIMEOUT = 0

_TEMPLATE_PATH = Path(__file__).with_suffix(".md")
_SECTIONS_DIR = _TEMPLATE_PATH.with_name("sections")


def _load_section(section_name: str) -> str:
    section_path = _SECTIONS_DIR / section_name
    return section_path.read_text()


def _load_prompt_templates() -> dict[str, str]:
    return {
        "agents_md": _TEMPLATE_PATH.read_text(),
        "workflow_harness": _load_section("workflow_harness.md"),
        "validate_artifacts": _load_section("validate_artifacts.md"),
        "pre_submit": _load_section("pre_submit.md"),
    }


def _md_inline(value: str) -> str:
    """Return a markdown-safe inline code span."""
    ticks = 1
    while "`" * ticks in value:
        ticks += 1
    fence = "`" * ticks
    return f"{fence}{value}{fence}"



def setup(source_dir: Path, config: dict) -> None:
    """One-time agent configuration.

    - Sets Claude-specific env vars (ANTHROPIC_BASE_URL, AUTH_TOKEN, IS_SANDBOX)
    - Writes .claude.json config
    - Writes CLAUDE.md into source_dir with libCRS tool docs + workflow
    """
    try:
        ver = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10,
        )
        logger.info("Claude Code CLI version: %s", ver.stdout.strip() or ver.stderr.strip())
    except Exception as e:
        logger.warning("Failed to get Claude Code version: %s", e)

    llm_api_url = config.get("llm_api_url", "")
    llm_api_key = config.get("llm_api_key", "")

    os.environ["IS_SANDBOX"] = "1"

    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if oauth_token:
        logger.info("CLAUDE_CODE_OAUTH_TOKEN found, using OAuth authentication (ignoring OSS_CRS LLM config)")
    elif llm_api_url and llm_api_key:
        os.environ["ANTHROPIC_BASE_URL"] = llm_api_url
        os.environ["ANTHROPIC_AUTH_TOKEN"] = llm_api_key
        os.environ["ANTHROPIC_API_KEY"] = ""
        logger.info("Claude Code configured with LiteLLM proxy: %s", llm_api_url)
        logger.info("ANTHROPIC_MODEL: %s", os.environ.get("ANTHROPIC_MODEL", "(default)"))
        logger.info("CLAUDE_CODE_SUBAGENT_MODEL: %s", os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL", "(default)"))
        logger.info("ANTHROPIC_DEFAULT_OPUS_MODEL: %s", os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "(default)"))
        logger.info("ANTHROPIC_DEFAULT_SONNET_MODEL: %s", os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "(default)"))
        logger.info("ANTHROPIC_DEFAULT_HAIKU_MODEL: %s", os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "(default)"))
    else:
        logger.warning("No LLM API URL/key set, Claude Code may not work")

    # Write Claude JSON config
    claude_config = {
        "numStartups": 0,
        "autoUpdaterStatus": "disabled",
        "userID": "-",
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "1.0.0",
        "projects": {
            str(source_dir): {
                "hasTrustDialogAccepted": True,
                "hasCompletedProjectOnboarding": True,
            }
        },
    }
    claude_json = Path.home() / ".claude.json"
    claude_json.write_text(json.dumps(claude_config))
    claude_json.chmod(0o600)
    logger.info("Wrote Claude config to %s", claude_json)

    # Global gitignore so runtime instructions never leak into patches.
    global_gitignore = Path.home() / ".gitignore"
    existing = ""
    if global_gitignore.exists():
        existing = global_gitignore.read_text(errors="replace")
    lines = [line.rstrip("\n") for line in existing.splitlines()]
    if "CLAUDE.md" not in lines:
        lines.append("CLAUDE.md")
    global_gitignore.write_text("\n".join(lines).rstrip("\n") + "\n")
    try:
        git_cfg = subprocess.run(
            ["git", "config", "--global", "core.excludesFile", str(global_gitignore)],
            capture_output=True,
        )
        if git_cfg.returncode != 0:
            logger.warning(
                "Failed to set global git excludesFile: %s",
                git_cfg.stderr.decode(errors="replace") if isinstance(git_cfg.stderr, bytes) else git_cfg.stderr,
            )
    except OSError as e:
        logger.warning("Failed to run git config for excludesFile: %s", e)

    logger.info("Agent setup complete")


def run(
    source_dir: Path,
    harness_dir: Path,
    work_dir: Path,
    *,
    language: str = "c",
    sanitizer: str = "address",
) -> bool:
    """Launch Claude Code in agentic mode to autonomously generate fuzzing harnesses.

    Claude Code downloads the fuzz-proj and target source, explores APIs,
    writes new harness(es), validates builds via libCRS, and copies the
    modified fuzz-proj to harness_dir/fuzz-proj (and, if it changed the target
    source, the modified source tree to harness_dir/target-source).

    Returns True if harness_dir/fuzz-proj was populated with files.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        templates = _load_prompt_templates()
    except OSError as e:
        logger.error("Failed to load prompt template(s): %s", e)
        return False

    fmt_vars = dict(source_dir=source_dir, work_dir=work_dir, harness_dir=harness_dir)

    workflow_section = templates["workflow_harness"].format(**fmt_vars)

    validate_section = templates["validate_artifacts"].format(
        work_dir=work_dir, harness_dir=harness_dir
    )

    pre_submit_section = templates["pre_submit"].format(harness_dir=harness_dir)

    claude_md = templates["agents_md"].format(
        language=language,
        sanitizer=sanitizer,
        source_dir=source_dir,
        work_dir=work_dir,
        harness_dir=harness_dir,
        workflow_section=workflow_section,
        validate_section=validate_section,
        pre_submit_section=pre_submit_section,
    )
    (source_dir / "CLAUDE.md").write_text(claude_md)

    target = os.environ.get("OSS_CRS_TARGET", source_dir.name)

    prompt_lines = [
        f"Generate new fuzzing harnesses for OSS-Fuzz project {_md_inline(target)} ({language}, {sanitizer}).",
        f"Goal: add harness(es) covering APIs not exercised by existing harnesses.",
        "",
    ]
    prompt_lines.append("Read CLAUDE.md for workflow, tools, and submission instructions.")
    prompt = "\n".join(prompt_lines)

    stdout_log = work_dir / "claude_stdout.log"
    stderr_log = work_dir / "claude_stderr.log"

    system_prompt = (
        f"You are an expert fuzzing engineer creating new coverage-maximizing harnesses "
        f"for OSS-Fuzz project `{target}` ({language}). Read and follow CLAUDE.md."
    )

    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--append-system-prompt", system_prompt,
    ]

    try:
        with open(stdout_log, "w") as out_f, open(stderr_log, "w") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                text=True,
                cwd=source_dir,
                start_new_session=True,
            )
            try:
                proc.stdin.write(prompt)  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]
                proc.wait(timeout=AGENT_TIMEOUT or None)
                logger.info("Claude Code exit code: %d", proc.returncode)
            except subprocess.TimeoutExpired:
                logger.warning("Claude Code timed out (%ds), killing process tree", AGENT_TIMEOUT)
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    time.sleep(2)
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
    except Exception as e:
        logger.error("Error running Claude Code: %s", e)
        return False

    subprocess.run(
        ["chmod", "-R", "og+rX", str(Path.home() / ".claude")],
        capture_output=True,
    )

    if proc.returncode != 0:
        logger.warning("Claude Code failed (rc=%d), see %s", proc.returncode, stderr_log)

    fuzz_proj_out = harness_dir / "fuzz-proj"
    fuzz_proj_files = [
        f for f in fuzz_proj_out.rglob("*") if f.is_file() and f.stat().st_size > 0
    ] if fuzz_proj_out.is_dir() else []
    if fuzz_proj_files:
        logger.info(
            "Agent populated %s with %d file(s)", fuzz_proj_out, len(fuzz_proj_files)
        )
        return True

    logger.info("Agent did not populate %s", fuzz_proj_out)
    return False
