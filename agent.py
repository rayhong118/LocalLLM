import asyncio
import utils
from browser_use import Agent, BrowserSession, ChatOllama

async def main():
    # Reverting to standard ChatOllama to ensure it runs.
    # num_ctx is not supported directly in this constructor version.
    llm = ChatOllama(
        model="qwen3.5-32k",
    )

    # Use BrowserSession with JS-friendly settings for dynamic sites
    browser = BrowserSession(
        headless=False,
        disable_security=True,                     # Allow cross-origin JS
        enable_default_extensions=False,            # Disable uBlock Origin (blocks Safeway's JS)
        minimum_wait_page_load_time=3,             # Wait 3s for JS to render
        wait_for_network_idle_page_load_time=5,    # Wait 5s for network idle
        args=[
            "--disable-blink-features=AutomationControlled",  # Hide automation
            "--disable-features=IsolateOrigins,site-per-process",  # Allow cross-origin
        ],
    )
    
    agent = Agent(
        task=(
            "Go to https://www.safeway.com/shop/deals/sale-prices.html.\n"
            "First, interact with an element containing '5100 Broadway' to open the store selection modal.\n"
            "Then, search for and select the nearest Safeway store to zip code 98008.\n"
            "Then find the current sale/promotion items in the ice-cream category.\n"
            "List the product names and their sale prices in Markdown format."
        ),
        llm=llm,
        browser=browser,
        use_vision=False,
        max_steps=15  # Safeway needs more steps for store selection + navigation
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
        
    print(f"\nFinal Result:\n{final_res}")
    
    # Save result to markdown
    utils.save_to_markdown(final_res)
    
    await browser.stop()

if __name__ == "__main__":
    asyncio.run(main())