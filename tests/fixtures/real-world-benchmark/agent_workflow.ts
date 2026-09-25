const REVIEW_ACTIONS = ["lgtm", "revise", "escalate"];

export function chooseReview(state: ReviewState) {
  return route(state, REVIEW_ACTIONS);
}
