# Contributing

Two rules, both enforced by the pre-commit hook and the validator:

1. **No bundle without evidence.** Every action, state, transition, surface and
   binding points at an `evidence_ledger.jsonl` entry with a locator. `inferred`
   and `hypothetical` never reach a production surface; a binding only becomes
   an executable handler with `verified_runtime` or `observed_trace` evidence.
2. **No PR with a red suite.** `python3 -m unittest discover -s tests` must pass,
   `examples/validation-bundle` must validate, and it must equal
   `scripts/init_action_bundle.py --example` (edit the script, regenerate with
   `--force`; never edit the YAML by hand).

Setup:

```bash
git clone https://github.com/FrancyJGLisboa/decision-system-forge
cd decision-system-forge
git config core.hooksPath .githooks
pip install pyyaml
python3 -m unittest discover -s tests -v
```

Good first contributions: a bundle for a public system (with its labeled
decision log), a real-browser test for the `ui` renderer, a real-server test
for the `mcp` renderer, or a schema clarification in `references/`.
Keep secrets out of bundles (`auth_env` names the variable; the validator
rejects values that look like tokens).
