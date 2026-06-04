## Validate the New Harness

A `build-project` retcode of 0 only means `build.sh` exited cleanly — it does **not**
prove your harness binary was produced, links correctly, or actually fuzzes. A typo in
the output name, a missing copy step, an unresolved symbol, or a harness that ignores its
input will all still exit 0. Before submitting, download the artifacts and **run each new
harness for a few seconds** to confirm it really works.

1. **Get the rebuild id.** `build-project` writes it to the dir you passed via
   `--response-dir`:

   ```bash
   rid=$(cat {work_dir}/build-resp/rebuild_id)
   ```

2. **Download the build output.** The `build` output is the contents of `$OUT/` (your
   compiled harness binaries).

   ```bash
   libCRS download-build-output build {work_dir}/build-out --rebuild-id "$rid"
   chmod +x {work_dir}/build-out/* 2>/dev/null || true
   ```

3. **Confirm each new harness binary is present and non-empty** in `{work_dir}/build-out`
   (for a harness you named `fuzz_foo`):

   ```bash
   test -s {work_dir}/build-out/fuzz_foo && echo "OK: built" || echo "MISSING: fuzz_foo"
   ```

   MISSING means `build.sh` did not produce/install it — fix `build.sh` and rebuild. Do not
   submit.

4. **Smoke-run each new harness for a short time, directly in this environment.** This is
   the real test — it proves the harness initializes, accepts input, and exercises the
   target. libFuzzer harnesses take a time budget; ~20–30s is plenty:

   ```bash
   mkdir -p {work_dir}/corpus-fuzz_foo
   {work_dir}/build-out/fuzz_foo -max_total_time=20 -rss_limit_mb=2560 \
       {work_dir}/corpus-fuzz_foo 2>&1 | tee {work_dir}/fuzz_foo.run.log
   ```

   For JVM (Jazzer) harnesses, run the generated wrapper the same way — it accepts the same
   libFuzzer flags: `{work_dir}/build-out/<harness> -max_total_time=20`.

   Read the output and judge:
   - **Healthy** — libFuzzer prints `#<N>` progress lines with a non-zero `exec/s`, and
     `cov:` grows over time, then it stops at the time limit and exits 0. This is what you
     want before submitting.
   - **Broken harness** — exits immediately, does ~0 runs, `exec/s` is 0, or prints an
     error at startup (missing `LLVMFuzzerTestOneInput`, load/link error, "no interesting
     inputs"). The harness is wrong — fix it, rebuild, and re-validate. Do **not** submit.
   - **Immediate crash** (non-zero exit, a `crash-*` file, or `SUMMARY: AddressSanitizer`
     within seconds) — most often a *harness* bug this early (e.g. mishandling `data`/`size`,
     reading past the buffer), not a genuine target finding. Investigate and fix the harness
     before submitting.

   Note: the binary runs in this agent container rather than the OSS-Fuzz runner image, so a
   `error while loading shared libraries` failure is an environment limitation, not a harness
   defect — in that one case, fall back to the artifact-existence check (step 3). Otherwise a
   clean short run is the strongest signal your harness is good.

Only submit once every new harness binary exists **and** completed a short run that
exercised the target (non-zero exec count, growing coverage, no startup error). Trust this
over the build retcode.
