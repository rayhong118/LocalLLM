# skills.py
from browser_use import Controller, BrowserSession
import logging
import asyncio
from site_skills.safeway import safeway_click_details

logger = logging.getLogger(__name__)
controller = Controller()

@controller.action('smart_click')
async def smart_click(text: str, browser: BrowserSession, index: int = 0):
    """Robust element clicking using multiple strategies: text matching, ARIA labels, and fuzzy search.
    
    Args:
        text: Target text or partial match of the element to click.
        index: If multiple matches exist, which one to click (0-indexed).
    """
    page = await browser.get_current_page()
    
    # Strategy 1: Visible Text (Exact)
    try:
        locator = page.get_by_text(text, exact=True).filter(visible=True)
        if await locator.count() > index:
            await locator.nth(index).click()
            return f"Success: Clicked exact text '{text}'"
    except Exception: pass

    # Strategy 2: Visible Text (Fuzzy)
    try:
        locator = page.get_by_text(text, exact=False).filter(visible=True)
        if await locator.count() > index:
            await locator.nth(index).click()
            return f"Success: Clicked fuzzy text matching '{text}'"
    except Exception: pass

    # Strategy 3: Role-based (Button/Link) with text
    for role in ["button", "link", "menuitem", "tab", "checkbox"]:
        try:
            locator = page.get_by_role(role, name=text, exact=False).filter(visible=True)
            if await locator.count() > index:
                await locator.nth(index).click()
                return f"Success: Clicked {role} with name '{text}'"
        except Exception: continue

    # Strategy 4: ARIA Label
    try:
        locator = page.get_by_label(text, exact=False).filter(visible=True)
        if await locator.count() > index:
            await locator.nth(index).click()
            return f"Success: Clicked element with label '{text}'"
    except Exception: pass

    # Strategy 5: Placeholder
    try:
        locator = page.get_by_placeholder(text, exact=False).filter(visible=True)
        if await locator.count() > index:
            await locator.nth(index).click()
            return f"Success: Clicked element with placeholder '{text}'"
    except Exception: pass

    return f"Failure: Could not find any clickable element matching '{text}'"

@controller.action('smart_type')
async def smart_type(label: str, text: str, browser: BrowserSession):
    """Finds an input field based on its label or placeholder and types text into it.
    
    Args:
        label: The identifying text for the input (label, placeholder, or nearby text).
        text: The text to type.
    """
    page = await browser.get_current_page()
    
    # Strategy 1: Direct Label
    try:
        locator = page.get_by_label(label, exact=False).filter(visible=True)
        if await locator.count() > 0:
            await locator.first.fill(text)
            return f"Success: Typed '{text}' into input with label '{label}'"
    except Exception: pass

    # Strategy 2: Placeholder
    try:
        locator = page.get_by_placeholder(label, exact=False).filter(visible=True)
        if await locator.count() > 0:
            await locator.first.fill(text)
            return f"Success: Typed '{text}' into input with placeholder '{label}'"
    except Exception: pass

    # Strategy 3: Find nearest input to a text label (common for Safeway/complex forms)
    try:
        # Use XPath to find an input following specific text
        xpath = f"//*[contains(text(), '{label}')]/following::input[1]"
        locator = page.locator(xpath).filter(visible=True)
        if await locator.count() > 0:
            await locator.first.fill(text)
            return f"Success: Typed '{text}' into input found near text '{label}'"
    except Exception: pass

    return f"Failure: Could not find an input field for label '{label}'"

@controller.action('scroll_to_text')
async def scroll_to_text(text: str, browser: BrowserSession):
    """Scrolls the page until the target text is visible.
    
    Args:
        text: The text to scroll into view.
    """
    page = await browser.get_current_page()
    try:
        locator = page.get_by_text(text, exact=False).first
        await locator.scroll_into_view_if_needed()
        return f"Success: Scrolled to text '{text}'"
    except Exception as e:
        return f"Failure: Could not scroll to text '{text}': {str(e)}"

@controller.action('nav_to_url')
async def nav_to_url(url: str, verify_text: str, browser: BrowserSession):
    """Navigates to a URL and verifies the load by checking for specific text.
    
    Args:
        url: The destination URL.
        verify_text: Text that should appear on the page if navigation was successful.
    """
    page = await browser.get_current_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        # Check for verification text
        locator = page.get_by_text(verify_text, exact=False)
        if await locator.count() > 0:
            return f"Success: Navigated to {url} and verified content '{verify_text}'"
        else:
            return f"Partial Success: Navigated to {url} but verify text '{verify_text}' not found."
    except Exception as e:
        return f"Failure: Navigation to {url} failed: {str(e)}"

def get_skill_descriptions():
    """Returns a formatted string of all registered skills and their docstrings.
    This allows the Orchestrator to learn about available skills dynamically.
    """
    lines = []
    for action_name, action in controller.registry.actions.items():
        # Get the first line of the docstring (the summary)
        desc = action.description.split('\n')[0].strip()
        lines.append(f"- {action_name}: {desc}")
    return "\n".join(lines)

# Register Site-Specific Skills manually
controller.action('safeway_click_details')(safeway_click_details)
