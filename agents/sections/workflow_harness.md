## Workflow

1. **Explore** — Download the fuzz-proj (`libCRS download-source fuzz-proj {work_dir}/fuzz-proj`). Read `build.sh` and existing harness source files to understand what APIs are already covered.
2. **Understand target** — Download target source (`libCRS download-source target-source {work_dir}/target-src`). Explore public API headers and source files to identify parsers, codecs, demuxers, or other entry points not yet exercised.
3. **Design** — Choose 1–3 new entry points to fuzz. Prefer functions that parse untrusted input or drive complex state machines not covered by existing harnesses.
4. **Implement** — Write new `.c`/`.cc` harness file(s) in `{work_dir}/fuzz-proj`. Update `build.sh` to compile each new harness binary and copy it to `$OUT/`.
5. **Build** — Run `libCRS build-project --fuzz-proj-dir {work_dir}/fuzz-proj ...` (libCRS diffs it against the base for you). Iterate on errors. Add `--target-source-dir {work_dir}/target-src` if the target source itself needs changes.
6. **Submit** — Once the build succeeds, copy the modified fuzz-proj to `{harness_dir}/fuzz-proj/`, and (only if you changed the target source) the modified source tree to `{harness_dir}/target-source/`.
