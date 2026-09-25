name: "Pydantic AI Attention Triage"
description: "Classify stale issues and PRs that may need a maintainer decision."
    record-attention-decision:
      description: "Classify one bounded candidate for deterministic host-side policy."
      # One decision per candidate, and the host script rejects any run that does
      # not classify every candidate exactly once. Must stay >= `_CANDIDATE_LIMIT`
      # in .github/scripts/issue_pr_attention_monitor.py — the default of 1 silently
      # drops the other 9 classifications and fails the run.
      max: 10
      runs-on: ubuntu-latest
      if: needs.detection.result == 'success' && needs.detection.outputs.detection_success == 'true'
      permissions:
        actions: read
        contents: read
        issues: write
        # Labels and assignees use the Issues REST endpoints for both issues and
        # PRs, but GitHub authorizes PR targets with this separate permission.
        pull-requests: write
      inputs:
        item_number:
          description: "Candidate issue or pull request number"
          required: true
          type: string
        next_actor:
          description: "Who must take the next meaningful action"
          required: true
          type: choice
          options: [maintainer, contributor, automation, none, uncertain]
        confidence:
          description: "Use high only when the evidence is clear"
          required: true
          type: choice
          options: [high, medium, low]
For every candidate, decide whether the **next meaningful action must come from a maintainer**:

- `maintainer` when a maintainer must review, decide scope or architecture, merge or close, answer a
  blocked contributor, or otherwise make the next project decision;
- `contributor` when the author or reporter must provide information or revise code;
- `automation` when CI, Pydanty, or another automated process is the next actor;
- `none` when no concrete action is due;
- `uncertain` when evidence conflicts or is incomplete.

Age, validity, importance, or an unanswered conversation alone are not enough. Request attention only
when the evidence clearly shows that a maintainer must make the next decision. The host validates every
item against the immutable snapshot, then applies fixed labels and assignment without model-generated text.

If there are candidates, use `Read` to load `attention-candidates.json`. If it reports truncation, continue
from the reported offset until the complete snapshot is loaded. Classify every candidate yourself, then
call `record_attention_decision` exactly once for every candidate. Make the independent decision calls in
parallel in one response when possible. Do not use `Task`, `LS`, `TodoWrite`, or read any other file.

The host applies assignment and attention labels only for high-confidence maintainer decisions. Other
items remain eligible after later activity changes who must act next. If the snapshot is empty, call
`noop` with a short fixed summary. Never include repository content in any output text.
