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
        user_data_dir=".browser_session",          # Persistent session/cookies
        args=[
            "--disable-blink-features=AutomationControlled",  # Hide automation
            "--disable-features=IsolateOrigins,site-per-process",  # Allow cross-origin
        ],
    )
    
    agent = Agent(
        task=(
            "SYSTEMATIC EXECUTION PROTOCOL:\n"
            "1. Navigate to https://www.safeway.com/loyalty/coupons-deals.html.\n"
            "2. Search for 'ice cream' to filter results.\n"
            "3. PROCESS ONE BY ONE: Scroll to a coupon, identify if it is a 'Premium' brand (Häagen-Dazs, Ben & Jerry's, Tillamook, etc.).\n"
            "4. For each Premium coupon: \n"
            "   a. Click 'Offer Details'. Wait for the modal to appear.\n"
            "   b. Verify if the coupon covers 'Chocolate' or 'Coffee' flavors by reading the item list in the modal.\n"
            "   c. If it qualifies, extract the product name and price.\n"
            "   d. CLOSE the modal before moving to the next coupon (click the 'X' or click outside).\n"
            "5. NO PREMATURE SUCCESS: Do not finish until you have checked ALL relevant visible 'ice cream' coupons on the page.\n"
            "6. REPORT: List all qualifying products in a Markdown table."
        ),
        llm=llm,
        browser=browser,
        use_vision=False,
        max_steps=100  # Safeway needs more steps for store selection + navigation + offer details
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