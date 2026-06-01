"""
crs-harness-gen-claude-code orchestrator.

Thin launcher that delegates harness generation to a swappable AI agent.
The agent (selected via CRS_AGENT env var) downloads the fuzz-proj and
target source, writes new harness source files, validates with build-project,
and populates harness_dir with the modified fuzz-proj (and, if changed, the
modified target source) which the orchestrator submits via submit_harness.

To add a new agent, create a module in agents/ implementing setup() and run().
"""

import importlib
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from libCRS.cli.main import init_crs_utils

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("harness_gen")

TARGET = os.environ.get("OSS_CRS_TARGET", "")
LANGUAGE = os.environ.get("FUZZING_LANGUAGE", "c")
SANITIZER = os.environ.get("SANITIZER", "address")
LLM_API_URL = os.environ.get("OSS_CRS_LLM_API_URL", "")
LLM_API_KEY = (
    open(os.environ["OSS_CRS_LLM_API_KEY_FILE"]).read().strip()
    if os.environ.get("OSS_CRS_LLM_API_KEY_FILE")
    else os.environ.get("OSS_CRS_LLM_API_KEY", "")
)

CRS_AGENT = os.environ.get("CRS_AGENT", "claude_code")

WORK_DIR = Path("/work")
SRC_DIR = Path("/src")
HARNESS_DIR = WORK_DIR / "harness-proj"

crs = None


def setup_source() -> Path | None:
    """Download build-output /src and prepare it as the reference directory."""
    safe_dir_proc = subprocess.run(
        ["git", "config", "--system", "--add", "safe.directory", "*"],
        capture_output=True,
    )
    if safe_dir_proc.returncode != 0:
        fallback_proc = subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", "*"],
            capture_output=True,
        )
        if fallback_proc.returncode != 0:
            logger.warning(
                "Failed to configure git safe.directory in both --system and --global scopes"
            )

    try:
        crs.download_build_output("src", SRC_DIR)
    except Exception as e:
        logger.error("Failed to download /src build output via libCRS: %s", e)
        return None

    worktree_dir = SRC_DIR.resolve()

    if (worktree_dir / ".git").exists():
        return worktree_dir

    logger.info("No .git found in %s, initializing git repo", worktree_dir)
    subprocess.run(["git", "init"], cwd=worktree_dir, capture_output=True, timeout=60)
    subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, timeout=60)
    commit_proc = subprocess.run(
        [
            "git",
            "-c",
            "user.name=crs-harness-gen-claude-code",
            "-c",
            "user.email=crs-harness-gen-claude-code@local",
            "commit",
            "-m",
            "initial source",
        ],
        cwd=worktree_dir,
        capture_output=True,
        timeout=60,
    )
    if commit_proc.returncode != 0:
        stderr = (
            commit_proc.stderr.decode(errors="replace")
            if isinstance(commit_proc.stderr, bytes)
            else str(commit_proc.stderr)
        )
        logger.error("Failed to create initial commit: %s", stderr.strip())
        return None

    return worktree_dir


def load_agent(agent_name: str):
    """Dynamically load an agent module from the agents package."""
    module_name = f"agents.{agent_name}"
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        logger.error("Failed to load agent '%s': %s", agent_name, e)
        sys.exit(1)


def main():
    logger.info(
        "Starting harness-gen: target=%s agent=%s language=%s sanitizer=%s",
        TARGET,
        CRS_AGENT,
        LANGUAGE,
        SANITIZER,
    )

    global crs
    crs = init_crs_utils()

    HARNESS_DIR.mkdir(parents=True, exist_ok=True)

    # Register Claude home as a log directory for post-run analysis.
    claude_home = Path.home() / ".claude"
    claude_home_backup = claude_home.with_name(".claude.pre-crs-backup")
    had_existing_claude_home = claude_home.exists() or claude_home.is_symlink()
    if claude_home_backup.exists() or claude_home_backup.is_symlink():
        rotated_backup = claude_home_backup.with_name(
            f"{claude_home_backup.name}-{int(time.time())}"
        )
        claude_home_backup.rename(rotated_backup)
    if had_existing_claude_home:
        claude_home.rename(claude_home_backup)

    try:
        crs.register_log_dir(claude_home)
        logger.info("Claude home registered as log dir at %s", claude_home)
    except Exception as e:
        logger.warning("Failed to register claude-home log dir: %s", e)
        if claude_home.exists() or claude_home.is_symlink():
            if claude_home.is_symlink() or claude_home.is_file():
                claude_home.unlink()
            else:
                shutil.rmtree(claude_home)
        if claude_home_backup.exists() or claude_home_backup.is_symlink():
            claude_home_backup.rename(claude_home)
            logger.info("Restored previous Claude home from backup")
        else:
            claude_home.mkdir(parents=True, exist_ok=True)

    # Register agent work directory as a log dir.
    agent_work_dir = WORK_DIR / "agent"
    try:
        crs.register_log_dir(agent_work_dir)
        logger.info("Agent work dir registered as log dir at %s", agent_work_dir)
    except Exception as e:
        logger.warning("Failed to register agent work log dir: %s", e)

    worktree_dir = setup_source()
    if worktree_dir is None:
        logger.error("Failed to set up source directory")
        sys.exit(1)

    logger.info("Reference source directory: %s", worktree_dir)

    agent = load_agent(CRS_AGENT)
    agent.setup(worktree_dir, {
        "llm_api_url": LLM_API_URL,
        "llm_api_key": LLM_API_KEY,
        "claude_home": str(claude_home),
    })

    agent_work_dir.mkdir(parents=True, exist_ok=True)
    success = bool(
        agent.run(
            source_dir=worktree_dir,
            harness_dir=HARNESS_DIR,
            work_dir=agent_work_dir,
            language=LANGUAGE,
            sanitizer=SANITIZER,
        )
    )

    if success:
        fuzz_proj_out = HARNESS_DIR / "fuzz-proj"
        target_source_out = HARNESS_DIR / "target-source"
        has_target_source = target_source_out.is_dir() and any(target_source_out.iterdir())
        logger.info(
            "Agent succeeded; submitting harness project (fuzz-proj=%s, target-source=%s)",
            fuzz_proj_out,
            target_source_out if has_target_source else "(none)",
        )
        try:
            crs.submit_harness(
                fuzz_proj_dir=fuzz_proj_out,
                target_source_dir=target_source_out if has_target_source else None,
                name=TARGET or HARNESS_DIR.name,
            )
            logger.info("Submitted harness project")
        except Exception as e:
            logger.error("Failed to submit harness project: %s", e)
            sys.exit(1)
    else:
        logger.warning("Agent did not populate harness_dir; nothing submitted")


if __name__ == "__main__":
    main()
