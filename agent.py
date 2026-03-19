import asyncio
from browser_use import Agent, Browser, ChatOllama

async def main():
    # Use browser_use's built-in ChatOllama (not langchain's)
    llm = ChatOllama(
        model="qwen2.5-coder:14b",
    )



    # 3. Pass headless config directly to Browser
    browser = Browser(headless=False)
    
    agent = Agent(
        task="1. Go to https://news.ycombinator.com\n2. Use the 'evaluate' tool to run: `document.querySelector('.titleline a').innerText`.\n3. Return the EXACT string result of that evaluation using the 'done' tool.",
        llm=llm,
        browser=browser,
        use_vision=False,
        max_steps=5
    )
    
    history = await agent.run()
    
    # history.final_result() might be None if it didn't use the 'done' action
    # We can also check the last action result
    final_res = history.final_result()
    if not final_res and history.history:
        # Fallback to the last extracted content if available
        last_step = history.history[-1]
        if last_step.result:
            final_res = last_step.result[-1].extracted_content
            
    if not final_res:
        final_res = "No result extracted"
        
    print(f"\nFinal Result: {final_res}")
    
    await browser.stop()

if __name__ == "__main__":
    asyncio.run(main())