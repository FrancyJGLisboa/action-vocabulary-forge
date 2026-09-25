            prompt = f"""\
Review all {len(documents)} documents in /workspace/input.
First spawn specialist subagents and assign the documents to them.
Each specialist must apply $expense-review-policy and include its policy_id and decision.
Wait for every review, write one JSON report per document and /workspace/output/summary.json,
and summarize the most important findings for the human approver.
"""
