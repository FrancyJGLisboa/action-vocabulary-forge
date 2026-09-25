# Integration Package

Use `prepare-integration` after a project reaches `work_system_mapped`:

```bash
python3 scripts/forge.py prepare-integration ./.forge/<system-id>
```

The package is a fail-closed implementation contract for an agent or engineer.
It translates each bounded decision into stable action IDs and names the work
needed to connect the discovered decision to a real environment.

It contains contracts for:

- observing and resolving target state;
- filtering actions through reviewed deterministic legality;
- calling JEV only with actions already proven legal;
- verifying target operations and postconditions before binding execution;
- stopping on terminal, unknown, errored, actionless, or uncertain states;
- recording privacy-safe decision telemetry;
- checking cross-contract consistency without calling the target.

Reconstruction evidence supports description, not execution. Consequently V1:

- emits every binding as `stub`, `executable: false`, `authority: none`;
- leaves activation state mappings unverified;
- does not choose a fallback action;
- leaves `max_iterations` unset and the loop blocked;
- preserves the Work System Map hash and source inventory;
- marks every integration verification check as pending.

An implementation agent may use the package as its work plan only when the user
separately asks it to build the integration. Promotion requires actual adapter
tests, deterministic preconditions, observed binding success and failure,
reviewed fallback behavior, finite loop limits, decision telemetry, trusted
labels, shadow evaluation, and a named release decision. Editing a stub to look
verified is not verification.

When implementation work is authorized, use `propose-bindings` and
`verify-bindings` for the first of those gates. The implementation agent may
propose typed locators, and a controlled host may exercise them, but the Forge
itself never invokes an arbitrary target. Successful and safe-negative traces
promote a candidate only to `verified_for_shadow`; it remains non-executable and
has no production authority. See [binding verification](binding-verification.md).
