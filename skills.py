# skills.py
# Rewritten to use CDP-compatible page.evaluate() instead of Playwright locators.
# browser-use's Page object is a CDP wrapper — .locator(), .get_by_text(), etc. do NOT exist.

from browser_use import Controller, BrowserSession
import logging
import asyncio
from site_skills.safeway import safeway_click_details, safeway_filter_category, safeway_get_all_deals, safeway_clip_coupon, safeway_clip_all_matching
from site_skills.weee import weee_filter_category, weee_get_all_deals, weee_add_to_favorites_by_indices

logger = logging.getLogger(__name__)
controller = Controller()

# Exclude distraction tools that contribute to interaction loops
controller.exclude_action('save_as_pdf')
controller.exclude_action('screenshot')


@controller.action('smart_click')
async def smart_click(text: str, browser: BrowserSession, index: int = 0):
    """Click an element using text matching, ARIA labels, fuzzy search, or semantic LLM selection.
    
    Args:
        text: Target text or partial match/intent of the element to click.
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

    data = _json.loads(result)
    if 'error' in data:
        # Standard matching failed. Try semantic LLM selection as a fallback.
        logger.info(f"Text matching failed for '{text}'. Retrying using LLM selection...")
        
        # Extract all visible potentially interactive elements
        candidates_json = await page.evaluate("""
        () => {
            const allElements = document.querySelectorAll('a, button, [role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="checkbox"], label, span, div, input, textarea');
            const list = [];
            for (let i = 0; i < allElements.length; i++) {
                const el = allElements[i];
                if (el.offsetParent === null) continue;
                
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                
                const text = (el.textContent || '').trim().replace(/\\s+/g, ' ');
                const aria = el.getAttribute('aria-label') || '';
                const placeholder = el.getAttribute('placeholder') || '';
                const title = el.getAttribute('title') || '';
                const role = el.getAttribute('role') || '';
                
                const mainText = text || aria || placeholder || title;
                if (!mainText) continue;
                
                list.push({
                    index: i,
                    tag: el.tagName.toLowerCase(),
                    text: text.substring(0, 80),
                    aria: aria.substring(0, 80),
                    placeholder: placeholder.substring(0, 80),
                    role: role
                });
            }
            return JSON.stringify(list.slice(0, 150));
        }
        """)
        
        candidates = _json.loads(candidates_json)
        if not candidates:
            return f"Failure: {data['error']} (and no interactive page elements were found for semantic fallback)"
            
        # Format the list of candidates for the LLM prompt
        candidates_str = ""
        for c in candidates:
            parts = []
            if c['text']: parts.append(f"text: '{c['text']}'")
            if c['aria']: parts.append(f"aria-label: '{c['aria']}'")
            if c['placeholder']: parts.append(f"placeholder: '{c['placeholder']}'")
            if c['role']: parts.append(f"role: '{c['role']}'")
            parts_str = ", ".join(parts)
            candidates_str += f"[{c['index']}] <{c['tag']}> {parts_str}\n"

        prompt = (
            f"You are a web automation assistant.\n"
            f"Select the single best element from the list below that semantically matches the user's intent to click: '{text}'.\n\n"
            f"Candidate Elements:\n"
            f"{candidates_str}\n"
            f"Select the element that is the closest semantic match for the intent '{text}'. "
            f"If absolutely none of the elements are a semantic match, respond with 'NONE'.\n\n"
            f"Your response must be JSON only in this format: {{\"selected_index\": int or null}}"
        )
        
        import httpx
        import config
        
        selected_index = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": config.LLM_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0, "num_ctx": 16384}
                    },
                    timeout=30
                )
                resp_data = resp.json()
                content = resp_data.get("message", {}).get("content", "{}")
                result_data = _json.loads(content)
                selected_index = result_data.get("selected_index")
        except Exception as e:
            logger.error(f"LLM semantic selection failed: {e}")
            
        if selected_index is not None:
            try:
                selected_index = int(selected_index)
            except (ValueError, TypeError):
                selected_index = None
                
        valid_indices = {c['index'] for c in candidates}
        if selected_index is not None and selected_index in valid_indices:
            # Click the selected index in the browser page
            click_result = await page.evaluate("""
            (targetIndex) => {
                const allElements = document.querySelectorAll('a, button, [role="button"], [role="link"], [role="menuitem"], [role="tab"], [role="checkbox"], label, span, div, input, textarea');
                if (targetIndex >= allElements.length) {
                    return JSON.stringify({error: 'Index out of bounds'});
                }
                const el = allElements[targetIndex];
                el.scrollIntoView({block: 'center'});
                el.click();
                return JSON.stringify({success: true, tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().substring(0, 80)});
            }
            """, selected_index)
            
            click_data = _json.loads(click_result)
            if 'success' in click_data:
                return f"Success: LLM matched '{text}' to element <{click_data['tag']}> (text: '{click_data.get('text', '')}') and clicked it."
            else:
                return f"Failure: Standard match failed with '{data['error']}'. Tried LLM fallback, but clicking selected element failed: {click_data.get('error', 'unknown error')}"
        else:
            return f"Failure: Standard match failed with '{data['error']}'. LLM was unable to find a semantic match."

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

# Register Weee Site-Specific Skills
controller.action('weee_filter_category')(weee_filter_category)
controller.action('weee_get_all_deals')(weee_get_all_deals)
controller.action('weee_add_to_favorites_by_indices')(weee_add_to_favorites_by_indices)
