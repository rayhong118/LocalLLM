# scratch/test_parser.py
import asyncio
from pydantic import BaseModel
from typing import List, Any
from llm_wrapper import JsonStrippingChatOllama

class MockSchema(BaseModel):
    thinking: str
    action: List[Any]

async def test():
    llm = JsonStrippingChatOllama(model="test")
    
    # Test XML tag parsing
    text = "<action>click(element=6314)</action>"
    repaired = llm._repair_json(text, MockSchema)
    print("XML Repair Result:", repaired.model_dump() if repaired else "Failed")
    
    # Test repetition truncation with JSON repair
    text_loop = '{"thinking": "I will search. ' + 'I will search. ' * 25 + '"action": []}'
    # Clean it first as ainvoke would
    text_loop = llm._clean_raw_content(text_loop)
    # Apply loop safety
    import re
    for seq in re.findall(r'(\s+\w+){3,}', text_loop):
         if text_loop.count(seq) > 20:
             text_loop = text_loop.split(seq)[0]
             if '{' in text_loop and '}' not in text_loop:
                 text_loop += '\n  "action": [], "thinking": "Loop detected" \n}'
             break
    
    print("Truncated Loop Text:", text_loop)
    try:
        repaired_loop = MockSchema.model_validate_json(text_loop)
        print("Loop JSON Repair Result: Success")
    except Exception as e:
        print("Loop JSON Repair Result: Failed -", e)

if __name__ == "__main__":
    asyncio.run(test())
