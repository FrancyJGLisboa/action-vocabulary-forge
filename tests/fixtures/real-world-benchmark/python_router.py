REVIEW_ACTIONS = ["accept", "revise", "escalate"]

def review_router(state):
    return choose(state, REVIEW_ACTIONS)
