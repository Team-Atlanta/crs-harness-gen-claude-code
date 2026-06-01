# =============================================================================
# crs-harness-gen-claude-code Harness Generation Module
# =============================================================================
# RUN phase: Downloads fuzz-proj and target source, generates new fuzzing
# harnesses using Claude Code, and submits the modified fuzz-proj.
#
# Uses host Docker socket (mounted by framework) to access snapshot images.
# =============================================================================

# These ARGs are required by the oss-crs framework template
ARG target_base_image
ARG crs_version

FROM claude-code-base

# Install libCRS (CLI + Python package)
COPY --from=libcrs . /libCRS
RUN pip3 install /libCRS \
    && python3 -c "from libCRS.base import DataType; print('libCRS OK')"

# Install crs-harness-gen-claude-code package (harness_gen + agents)
COPY pyproject.toml /opt/crs-harness-gen-claude-code/pyproject.toml
COPY harness_gen.py /opt/crs-harness-gen-claude-code/harness_gen.py
COPY agents/ /opt/crs-harness-gen-claude-code/agents/
RUN pip3 install /opt/crs-harness-gen-claude-code

CMD ["run_harness_gen"]
