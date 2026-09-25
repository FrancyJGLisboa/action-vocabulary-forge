const REVIEW_ACTIONS = ["lgtm", "revise", "escalate"];

export function reviewPlan(state: unknown) {
  return choose(state, REVIEW_ACTIONS);
}
