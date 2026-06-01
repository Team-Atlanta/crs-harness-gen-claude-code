import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents import claude_code


def test_run_does_not_pass_debug_file_flag(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    harness_dir = tmp_path / "harness-proj"
    harness_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    monkeypatch.setattr(
        claude_code,
        "_load_prompt_templates",
        lambda: {
            "agents_md": "{workflow_section}\n{pre_submit_section}",
            "workflow_harness": "workflow",
            "pre_submit": "checklist",
        },
    )
    monkeypatch.setattr(claude_code, "AGENT_TIMEOUT", 0)
    monkeypatch.setattr(
        claude_code.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0, "stderr": b""})(),
    )

    popen_calls: list[list[str]] = []

    class FakeStdin:
        def __init__(self) -> None:
            self.buffer = ""

        def write(self, data: str) -> None:
            self.buffer += data

        def close(self) -> None:
            return None

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append(list(cmd))
            self.stdin = FakeStdin()
            self.returncode = 0
            self.pid = 12345

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(claude_code.subprocess, "Popen", FakePopen)

    produced = claude_code.run(
        source_dir,
        harness_dir,
        work_dir,
    )

    # harness_dir is empty → should return False
    assert produced is False
    assert len(popen_calls) == 1
    cmd = popen_calls[0]
    assert "--dangerously-skip-permissions" in cmd
    assert "--append-system-prompt" in cmd
    assert "--debug-file" not in cmd


def test_run_returns_true_when_harness_dir_populated(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    harness_dir = tmp_path / "harness-proj"
    harness_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    monkeypatch.setattr(
        claude_code,
        "_load_prompt_templates",
        lambda: {
            "agents_md": "{workflow_section}\n{pre_submit_section}",
            "workflow_harness": "workflow",
            "pre_submit": "checklist",
        },
    )
    monkeypatch.setattr(claude_code, "AGENT_TIMEOUT", 0)
    monkeypatch.setattr(
        claude_code.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0, "stderr": b""})(),
    )

    class FakeStdin:
        def write(self, data: str) -> None:
            pass

        def close(self) -> None:
            pass

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            # Simulate agent writing the modified fuzz-proj to harness_dir/fuzz-proj
            fuzz_proj_out = harness_dir / "fuzz-proj"
            fuzz_proj_out.mkdir(parents=True, exist_ok=True)
            (fuzz_proj_out / "build.sh").write_text("#!/bin/bash\n")
            self.stdin = FakeStdin()
            self.returncode = 0
            self.pid = 12345

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(claude_code.subprocess, "Popen", FakePopen)

    produced = claude_code.run(
        source_dir,
        harness_dir,
        work_dir,
    )

    assert produced is True
