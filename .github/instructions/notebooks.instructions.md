---
applyTo: "**/*.ipynb"
description: "Use when editing Jupyter notebooks for local LLM inference, fine-tuning, and tool-calling experiments."
---

# Notebook Instructions

- Keep each notebook scoped to one experiment objective.
- Add or update markdown cells to document: goal, inputs, model/runtime config, and expected outputs.
- Execute modified code cells in order to validate stateful correctness.
- Prefer deterministic examples where possible (fixed seeds, explicit params).
- Keep shell-command demonstrations non-destructive by default.
- Clearly separate setup, data prep, model execution, and evaluation sections.
- When adding dependencies, also update the project dependency file used in this repo.
- Avoid embedding large binary outputs in notebooks unless explicitly requested.
