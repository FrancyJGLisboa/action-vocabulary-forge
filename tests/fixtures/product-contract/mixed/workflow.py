NEXT_ACTIONS = ("send", "request_information", "escalate")
MAX_RETRIES = 2


def choose_next_action(state, classifier):
    return classifier.choice(state, NEXT_ACTIONS)


def retry_limit_reached(state):
    return state["retries"] >= MAX_RETRIES


def draft_message(state, llm):
    return llm.generate("Write a response for the current case: " + state["summary"])
