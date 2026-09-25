def route_with_llm(request, model):
    return model.generate(
        "Choose exactly one: accept, revise, or escalate."
    )
