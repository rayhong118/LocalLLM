import sys
import os
import asyncio

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from site_skills.sofi import sofi_run_pre_flight

class DummyBrowser:
    async def get_current_page(self):
        return None

async def test():
    prompt = "Monitor if any software engineer or manager openings are released on sofi jobs page"
    context = ""
    log_path = "scratch/test_sofi_skill.log"
    llm = None
    browser = DummyBrowser()
    
    print("Running sofi_run_pre_flight...")
    result = await sofi_run_pre_flight(browser, prompt, context, log_path, llm)
    print("\n--- SKILL RESULT ---")
    sys.stdout.reconfigure(encoding='utf-8')
    print(result)
    print("--------------------")

if __name__ == "__main__":
    asyncio.run(test())
