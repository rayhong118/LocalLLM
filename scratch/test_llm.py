import asyncio
import sys
import os

# Add parent dir to sys.path
sys.path.append(os.getcwd())

from llm_wrapper import JsonStrippingChatOllama
from langchain_core.messages import HumanMessage
import config

async def test_llm():
    llm = JsonStrippingChatOllama(
        model=config.LLM_MODEL,
        ollama_options={"temperature": 0}
    )
    
    # Test case 1: Thinking before JSON
    raw_content = "Here is my thought: I need to click the search bar. ```json\n{\"action\": [{\"click_element\": {\"index\": 123}}]}```"
    cleaned = llm._clean_raw_content(raw_content)
    print(f"Test 1 Cleaned: {cleaned}")
    
    # Test case 2: Tag-based thinking
    raw_content_2 = "<thought>Thinking about food.</thought>{\"action\": []}"
    cleaned_2 = llm._clean_raw_content(raw_content_2)
    print(f"Test 2 Cleaned: {cleaned_2}")

if __name__ == "__main__":
    asyncio.run(test_llm())
