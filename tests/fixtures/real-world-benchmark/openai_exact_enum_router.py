from enum import Enum
from pydantic import BaseModel
from textwrap import dedent
import openai

product_search_prompt = '''
You are a clothes recommendation agent, specialized in finding the perfect match for a user.
Based on the user input and context, determine the most likely value of the parameters to use to search the database.
Here are the different categories that are available on the website:
- shoes: boots, sneakers, sandals
- jackets: winter coats, cardigans, parkas, rain jackets
- tops: shirts, blouses, t-shirts, crop tops, sweaters
- bottoms: jeans, skirts, trousers, joggers
'''

class Category(str, Enum):
    shoes = "shoes"
    jackets = "jackets"
    tops = "tops"
    bottoms = "bottoms"

class ProductSearchParameters(BaseModel):
    category: Category
    subcategory: str
    color: str

def get_response(user_input, context):
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[{"role": "user", "content": f"CONTEXT: {context} USER INPUT: {user_input}"}],
        tools=[{"type": "function", "function": {"name": "search_products"}}],
    )
    return response
