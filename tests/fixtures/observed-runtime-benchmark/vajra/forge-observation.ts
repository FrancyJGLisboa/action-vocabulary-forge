import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";

const PLAN_REVIEW_ACTIONS = new Set(["lgtm", "revise", "escalate"]);

export async function observePlanReviewDecision(opts: {
  issueIdentifier: string;
  attempt: number;
  stageId: string;
  visit: number;
  stageStatus: string;
  selectedAction: string | null;
  availableActions: Array<string | undefined>;
  nextStageId: string;
}): Promise<void> {
  const destination = process.env.FORGE_OBSERVATION_LOG?.trim();
  const selectedAction = opts.selectedAction?.trim() ?? "";
  const availableActions = [...new Set(
    opts.availableActions
      .map((value) => value?.trim() ?? "")
      .filter((value) => PLAN_REVIEW_ACTIONS.has(value)),
  )].sort();
  if (
    !destination
    || !opts.stageId.includes("review")
    || !PLAN_REVIEW_ACTIONS.has(selectedAction)
    || !availableActions.includes(selectedAction)
  ) {
    return;
  }

  const observedAt = new Date().toISOString();
  const record = {
    event_id: randomUUID(),
    case_id: `${opts.issueIdentifier}:attempt:${opts.attempt}`,
    opportunity_id: "plan_review_outcome",
    occurred_at: observedAt,
    actor_type: "agent",
    state_before: {
      stage_id: opts.stageId,
      stage_status: opts.stageStatus,
      visit: opts.visit,
    },
    available_actions: availableActions,
    selected_action: selectedAction,
    state_after: {
      next_stage_id: opts.nextStageId,
    },
    source_ref: `vajra:pipeline:${opts.stageId}`,
  };

  try {
    await mkdir(dirname(destination), { recursive: true });
    await appendFile(destination, `${JSON.stringify(record)}\n`, "utf8");
  } catch {
    // Observation is opt-in telemetry and must never change pipeline behavior.
  }
}
