# crs-harness-gen-claude-code

A [CRS](https://github.com/oss-crs) (Cyber Reasoning System) that uses [Claude Code](https://docs.anthropic.com/en/docs/claude-code) to autonomously generate new coverage-maximizing fuzzing harnesses for OSS-Fuzz projects.

The agent downloads the OSS-Fuzz project and its upstream target source, explores the public API surface, designs and writes new harness source files targeting entry points not covered by existing harnesses, builds them via libCRS, iterates until the build succeeds, and submits the modified fuzz project for fuzzing.

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│ harness_gen.py (orchestrator)                                       │
│                                                                     │
│  1. Download build-output /src as the reference source tree          │
│     crs.download_build_output(src)                                  │
│         │                                                            │
│         ▼                                                            │
│  2. Launch Claude Code agent with reference source + CLAUDE.md       │
│     claude -p --dangerously-skip-permissions                        │
│       --append-system-prompt <rules>                                │
└─────────┬───────────────────────────────────────────────────────────┘
          │ stdin: prompt (target, language, sanitizer)
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Claude Code (autonomous agent)                                      │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐                   │
│  │ Explore  │───▶│  Design  │───▶│   Build      │                   │
│  │          │    │ + write  │    │              │                   │
│  │ download │    │ harness  │    │ build-project│──▶ Builder        │
│  │ fuzz-proj│    │ edit     │    │              │    sidecar        │
│  │ + target │    │ build.sh │    │              │◀── retcode        │
│  └──────────┘    └──────────┘    └──────┬───────┘                   │
│                       ▲                 │                           │
│                       │                 │                           │
│                       └── retry ◀── fail?                           │
│                                         │ build ok                  │
│                                         ▼                           │
│              Copy modified fuzz-proj (+ target-source if changed)   │
│                   to /work/harness-proj/                            │
└─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ harness_gen.py               │
│ submit_harness(fuzz-proj,    │──▶ oss-crs framework
│   target-source?)            │
└─────────────────────────────┘
```

1. **`run_harness_gen`** downloads the build-output `/src` tree as a reference source directory (initializing a git repo if none exists) and launches the agent.
2. **Claude Code** runs in a single session with generated `CLAUDE.md` instructions. It downloads a fresh OSS-Fuzz project (`fuzz-proj`) and upstream target source (`target-source`) via libCRS.
3. The agent explores existing harnesses and the target API, designs 1–3 new harnesses for uncovered entry points (parsers, codecs, demuxers, format readers), writes the harness source, and updates `build.sh` to compile and install each binary to `$OUT/`.
4. It validates with **libCRS** `build-project` (which diffs the working dirs against their downloaded bases and triggers a rebuild) through the builder sidecar, iterating freely until the build succeeds.
5. Once the build passes, the agent copies the complete modified fuzz project to `/work/harness-proj/fuzz-proj/` — and, only if it changed upstream source, the modified tree to `/work/harness-proj/target-source/`. The orchestrator submits these with `crs.submit_harness(...)` and exits.

The agent is language-agnostic — it writes harness source and build scripts while the builder sidecar handles compilation. The sanitizer type is passed to the agent for context.

## Project structure

```
harness_gen.py        # Orchestrator: download source → agent → submit_harness
pyproject.toml         # Package config (run_harness_gen entry point)
bin/
  compile_target       # Builder phase: compiles the target project
agents/
  claude_code.py       # Claude Code agent (default)
  claude_code.md       # CLAUDE.md template with libCRS tool docs
  sections/            # Dynamic CLAUDE.md section partial templates
  template.py          # Stub for creating new agents
oss-crs/
  crs.yaml             # CRS metadata (type: harness-gen, supported languages, etc.)
  example-compose.yaml # Example crs-compose configuration
  base.Dockerfile      # Base image: Ubuntu + Node.js + Claude Code CLI + Python
  builder.Dockerfile   # Build phase image
  harness_gen.Dockerfile # Run phase image
  docker-bake.hcl      # Docker Bake config for the base image
  sample-litellm-config.yaml  # LiteLLM proxy config template
```

## Prerequisites

- **[oss-crs](https://github.com/oss-crs/oss-crs)** — the CRS framework (`crs-compose` CLI)

Builder and runner sidecars are injected automatically by the framework — no separate builder setup is needed.

## Quick start

### 1. Configure `crs-compose.yaml`

Copy `oss-crs/example-compose.yaml` and update the paths:

```yaml
crs-claude-code:
  source:
    local_path: /path/to/crs-harness-gen-claude-code
  cpuset: "2-7"
  memory: "16G"
  llm_budget: 10
  additional_env:
    CRS_AGENT: claude_code
    ANTHROPIC_MODEL: claude-opus-4-6
```

### 2. Optional LiteLLM setup

If you want OSS-CRS to inject an external LiteLLM endpoint, uncomment the `llm_config` block and make sure `EXTERNAL_LITELLM_API_BASE` and `EXTERNAL_LITELLM_API_KEY` are set. `oss-crs/sample-litellm-config.yaml` remains available as a reference template for LiteLLM-backed setups.

### 3. Run with oss-crs

```bash
crs-compose up -f crs-compose.yaml
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CRS_AGENT` | `claude_code` | Agent module name (maps to `agents/<name>.py`) |
| `ANTHROPIC_MODEL` | unset | Primary Claude model read from the environment |
| `CLAUDE_CODE_SUBAGENT_MODEL` | unset | Optional model for Claude Code subagents |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | unset | Optional env override for the `opus` alias |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | unset | Optional env override for the `sonnet` alias |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | unset | Optional env override for the `haiku` alias |
| `AGENT_TIMEOUT` | `0` (no limit) | Agent timeout in seconds (0 = run until budget exhausted) |

These are standard Claude Code env vars. The CRS reads whatever values you provide in `additional_env`; if you want reproducible benchmarking, set each one explicitly.

Available models:
- `claude-opus-4-6`
- `claude-opus-4-5-20251101`
- `claude-opus-4-1-20250805`
- `claude-sonnet-4-6`
- `claude-sonnet-4-5-20250929`
- `claude-sonnet-4-20250514`
- `claude-haiku-4-5-20251001`

## Runtime behavior

- **Execution**: `claude -p --dangerously-skip-permissions --append-system-prompt <rules>` (non-interactive, full permissions)
- **Instruction file**: `CLAUDE.md` generated per run in the reference source tree
- **LiteLLM proxy**: Framework provides `OSS_CRS_LLM_API_URL` + `OSS_CRS_LLM_API_KEY` (the key may come from `OSS_CRS_LLM_API_KEY_FILE`); agent remaps to `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`

Debug artifacts:
- Log directory: `/root/.claude` (registered via `register-log-dir`)
- Per-run logs: `/work/agent/claude_stdout.log`, `/work/agent/claude_stderr.log`
- Claude Code internal logs: `/root/.claude/debug/`

## Harness submission

The agent is instructed to satisfy these criteria before writing to the harness output directory:

1. **Builds** — `build-project` returns retcode 0
2. **New binaries installed** — each new harness binary appears in the build output, installed to `$OUT/`
3. **New coverage** — the harness(es) target APIs not exercised by existing harnesses
4. **Complete project** — `harness-proj/fuzz-proj/` contains all original fuzz-proj files plus the new harness source(s), and `build.sh` compiles and installs them

`harness-proj/target-source/` is included only if the agent changed upstream source; otherwise it is omitted entirely.

Once the agent has populated `harness-proj/fuzz-proj/`, the orchestrator submits the directories with `crs.submit_harness(fuzz_proj_dir, target_source_dir, name)` and exits. Submission is final.

## Adding a new agent

1. Copy `agents/template.py` to `agents/my_agent.py`.
2. Implement `setup()` and `run()`.
3. Set `CRS_AGENT=my_agent`.

The agent receives:
- **setup(source_dir, config)** config keys:
  - `llm_api_url` — optional LiteLLM base URL
  - `llm_api_key` — optional LiteLLM key
  - `claude_home` — path for Claude Code state/logs
- **source_dir** — reference source tree (git repo of the compiled target)
- **harness_dir** — output directory; populate `harness_dir/fuzz-proj/` (and optionally `harness_dir/target-source/`)
- **work_dir** — scratch space (downloads, build responses, logs)
- **language** — target language (c, c++, jvm)
- **sanitizer** — sanitizer type passed for context

The agent has access to libCRS commands (builder sidecar resolved automatically via the `BUILDER_MODULE` env var set by the framework):
- `libCRS download-source fuzz-proj <dst_dir>` — fetch a fresh OSS-Fuzz project directory (Dockerfile, `build.sh`, existing harness sources)
- `libCRS download-source target-source <dst_dir>` — fetch a fresh upstream target source tree
- `libCRS build-project --response-dir <dir> --fuzz-proj-dir <dir> [--target-source-dir <dir>]` — diff the working dirs against their bases and build (at least one source dir required)

For transparent diagnostics, always inspect the response dir logs:
- Build: `stdout.log`, `stderr.log`, `retcode`
