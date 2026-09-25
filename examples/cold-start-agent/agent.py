"""Small research agent used to test cold-start decision discovery."""

NEXT_ACTIONS = ("search_more", "revise", "accept", "escalate")
MAX_RETRIES = 3


def choose_next_research_step(state, classifier):
    if state["retries"] >= MAX_RETRIES:
        return "escalate"
    return classifier.choice(state, NEXT_ACTIONS)


def draft_final_answer(context, llm):
    return llm.generate("Draft the final answer from this evidence: " + context)
