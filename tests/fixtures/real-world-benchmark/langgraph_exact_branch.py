def should_continue(state: State):
    if len(state["messages"]) > 0:
        return END
    return "call_model"
