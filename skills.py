# skills.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — .locator(), .get_by_text(), etc. do NOT exist.

from browser_use import Controller, BrowserSession
import logging
import asyncio
from site_skills.safeway import safeway_click_details, safeway_filter_category, safeway_get_all_deals, safeway_clip_coupon, safeway_clip_all_matching

logger = logging.getLogger(__name__)
controller = Controller()

# Exclude distraction tools that contribute to interaction loops
controller.exclude_action('save_as_pdf')
controller.exclude_action('screenshot')


@controller.action('smart_click')
async def smart_click(text: str, browser: BrowserSession, index: int = 0):
    """Robust element clicking using multiple strategies: text matching, ARIA labels, and fuzzy search.
    
    Args:
        text: Target text or partial match of the element to click.
        index: If multiple matches exist, which one to click (0-indexed).
    """
    page = await browser.get_current_page()
    import json as _json

    result = await page.evaluate("""
    (targetText, targetIndex) => {
        const textLower = targetText.toLowerCase();
        
        // Strategy 1: Exact visible text match
        const allElements = document.querySelectorAll('a, button, [role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="checkbox"], label, span, div');
        let matches = [];
        
        for (const el of allElements) {
            if (el.offsetParent === null) continue;
            const elText = (el.textContent || '').trim();
            if (elText.toLowerCase() === textLower) {
                matches.push({el, strategy: 'exact_text'});
            }
        }
        
        // Strategy 2: Fuzzy text match (contains)
        if (matches.length <= targetIndex) {
            for (const el of allElements) {
                if (el.offsetParent === null) continue;
                const elText = (el.textContent || '').trim().toLowerCase();
                if (elText.includes(textLower) && !matches.some(m => m.el === el)) {
                    matches.push({el, strategy: 'fuzzy_text'});
                }
            }
        }
        
        // Strategy 3: ARIA label match
        if (matches.length <= targetIndex) {
            for (const el of allElements) {
                if (el.offsetParent === null) continue;
                const label = el.getAttribute('aria-label') || '';
                if (label.toLowerCase().includes(textLower) && !matches.some(m => m.el === el)) {
                    matches.push({el, strategy: 'aria_label'});
                }
            }
        }
        
        // Strategy 4: Placeholder match
        if (matches.length <= targetIndex) {
            const inputs = document.querySelectorAll('input, textarea');
            for (const el of inputs) {
                if (el.offsetParent === null) continue;
                const ph = el.getAttribute('placeholder') || '';
                if (ph.toLowerCase().includes(textLower) && !matches.some(m => m.el === el)) {
                    matches.push({el, strategy: 'placeholder'});
                }
            }
        }
        
        if (matches.length === 0) {
            return JSON.stringify({error: 'No clickable element matching "' + targetText + '"'});
        }
        
        if (targetIndex >= matches.length) {
            return JSON.stringify({error: 'Index ' + targetIndex + ' out of bounds (only ' + matches.length + ' matches)'});
        }
        
        const target = matches[targetIndex];
        target.el.scrollIntoView({block: 'center'});
        target.el.click();
        return JSON.stringify({success: true, strategy: target.strategy, text: (target.el.textContent || '').trim().substring(0, 80)});
    }
    """, text, index)

    data = __import__('json').loads(result)
    if 'error' in data:
        return f"Failure: {data['error']}"
    return f"Success: Clicked {data['strategy']} matching '{text}' (text: '{data.get('text', '')}')"


@controller.action('smart_type')
async def smart_type(label: str, text: str, browser: BrowserSession):
    """Finds an input field based on its label or placeholder and types text into it.
    
    Args:
        label: The identifying text for the input (label, placeholder, or nearby text).
        text: The text to type.
    """
    page = await browser.get_current_page()

    result = await page.evaluate("""
    (labelText, inputText) => {
        const labelLower = labelText.toLowerCase();
        
        // Strategy 1: Direct label with 'for' attribute
        const labels = document.querySelectorAll('label');
        for (const lbl of labels) {
            if (lbl.textContent.toLowerCase().includes(labelLower)) {
                const forId = lbl.getAttribute('for');
                if (forId) {
                    const input = document.getElementById(forId);
                    if (input) {
                        input.focus();
                        input.value = inputText;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                        return JSON.stringify({success: true, strategy: 'label_for'});
                    }
                }
            }
        }
        
        // Strategy 2: Placeholder match
        const inputs = document.querySelectorAll('input, textarea');
        for (const input of inputs) {
            if (input.offsetParent === null) continue;
            const ph = (input.getAttribute('placeholder') || '').toLowerCase();
            if (ph.includes(labelLower)) {
                input.focus();
                input.value = inputText;
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                return JSON.stringify({success: true, strategy: 'placeholder'});
            }
        }
        
        // Strategy 3: ARIA label match
        for (const input of inputs) {
            if (input.offsetParent === null) continue;
            const aria = (input.getAttribute('aria-label') || '').toLowerCase();
            if (aria.includes(labelLower)) {
                input.focus();
                input.value = inputText;
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                return JSON.stringify({success: true, strategy: 'aria_label'});
            }
        }
        
        // Strategy 4: Nearest input after label text
        for (const lbl of labels) {
            if (lbl.textContent.toLowerCase().includes(labelLower)) {
                const nextInput = lbl.parentElement?.querySelector('input, textarea') ||
                                  lbl.nextElementSibling;
                if (nextInput && (nextInput.tagName === 'INPUT' || nextInput.tagName === 'TEXTAREA')) {
                    nextInput.focus();
                    nextInput.value = inputText;
                    nextInput.dispatchEvent(new Event('input', {bubbles: true}));
                    nextInput.dispatchEvent(new Event('change', {bubbles: true}));
                    return JSON.stringify({success: true, strategy: 'nearest_input'});
                }
            }
        }
        
        return JSON.stringify({error: 'Could not find input field for label "' + labelText + '"'});
    }
    """, label, text)

    data = __import__('json').loads(result)
    if 'error' in data:
        return f"Failure: {data['error']}"
    return f"Success: Typed '{text}' into input ({data['strategy']}) for label '{label}'"


@controller.action('scroll_to_text')
async def scroll_to_text(text: str, browser: BrowserSession):
    """Scrolls the page until the target text is visible.
    
    Args:
        text: The text to scroll into view.
    """
    page = await browser.get_current_page()
    try:
        result = await page.evaluate("""
        (targetText) => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.includes(targetText)) {
                    walker.currentNode.parentElement.scrollIntoView({block: 'center', behavior: 'smooth'});
                    return JSON.stringify({success: true});
                }
            }
            return JSON.stringify({error: 'Text not found'});
        }
        """, text)
        
        data = __import__('json').loads(result)
        if 'error' in data:
            return f"Failure: Could not scroll to text '{text}': {data['error']}"
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
        await page.goto(url)
        await asyncio.sleep(3)  # Wait for SPA to render
        
        # Verify text presence using JS
        result = await page.evaluate("""
        (verifyText) => {
            return document.body.innerText.toLowerCase().includes(verifyText.toLowerCase()) ? 'found' : 'not_found';
        }
        """, verify_text)
        
        if result == 'found':
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
    try:
        # browser-use Registry path can change between versions; try nested registry
        registry = getattr(controller.registry, 'registry', controller.registry)
        actions = getattr(registry, 'actions', {})
        
        for action_name, action in actions.items():
            # Get the first line of the docstring (the summary)
            if hasattr(action, 'description') and action.description:
                desc = action.description.split('\n')[0].strip()
                lines.append(f"- {action_name}: {desc}")
            else:
                lines.append(f"- {action_name}")
    except Exception as e:
        print(f"DEBUG - get_skill_descriptions failed: {e}")
        return "No skill descriptions available."
        
    return "\n".join(lines)

# Register Site-Specific Skills manually
controller.action('safeway_click_details')(safeway_click_details)
controller.action('safeway_filter_category')(safeway_filter_category)
controller.action('safeway_get_all_deals')(safeway_get_all_deals)
controller.action('safeway_clip_coupon')(safeway_clip_coupon)
controller.action('safeway_clip_all_matching')(safeway_clip_all_matching)
