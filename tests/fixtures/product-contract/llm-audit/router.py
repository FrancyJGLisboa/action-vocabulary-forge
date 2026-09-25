from openai import OpenAI


client = OpenAI()
ROUTES = ["billing", "technical", "account"]


def route_request(message: str):
    prompt = f"Choose exactly one route: billing, technical, account. Request: {message}"
    return client.responses.create(model="decision-model", input=prompt)
