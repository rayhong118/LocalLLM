# stealth.py — Inject anti-detection JavaScript and DOM cleanup into browser pages
"""
Two-part injection for Playwright browser sessions:
1. Stealth: Patches navigator.webdriver, chrome.runtime, and other bot fingerprint leaks.
2. DOM Cleanup: Marks non-essential DOM elements with data-browser-use-exclude="true"
   so browser-use's serializer skips them, drastically reducing context token usage.
"""

STEALTH_JS = """
// Override navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// Override navigator.plugins (headless Chrome has empty plugins)
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'internal-nacl-plugin' }
    ]
});

// Override navigator.languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});

// Override chrome.runtime to prevent detection via missing chrome object
if (!window.chrome) { window.chrome = {}; }
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        connect: function() {},
        sendMessage: function() {}
    };
}

// Override permissions query for notifications
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// Prevent detection via iframe contentWindow
try {
    const iframeProto = HTMLIFrameElement.prototype;
    const origGetter = Object.getOwnPropertyDescriptor(iframeProto, 'contentWindow').get;
    Object.defineProperty(iframeProto, 'contentWindow', {
        get: function() {
            const iframe = origGetter.call(this);
            if (iframe) {
                try {
                    Object.defineProperty(iframe.navigator, 'webdriver', { get: () => undefined });
                } catch(e) {}
            }
            return iframe;
        }
    });
} catch(e) {}
"""

# JavaScript that marks junk DOM elements for exclusion by browser-use's serializer.
# browser-use skips any element with data-browser-use-exclude="true"
DOM_CLEANUP_JS = """
(function() {
    const EXCLUDE_ATTR = 'data-browser-use-exclude';

    // 1. Tag-based exclusions: elements that are never useful for agent interaction
    const JUNK_TAGS = ['noscript', 'svg', 'path', 'defs', 'clippath',
                       'lineargradient', 'radialgradient', 'symbol', 'use',
                       'footer', 'aside'];
    JUNK_TAGS.forEach(tag => {
        document.querySelectorAll(tag).forEach(el => {
            el.setAttribute(EXCLUDE_ATTR, 'true');
        });
    });

    // 2. Class/role-based exclusions: common junk patterns across websites
    const JUNK_SELECTORS = [
        // Ads and tracking
        '[class*="ad-"]', '[class*="ads-"]', '[class*="advert"]',
        '[id*="google_ads"]', '[id*="ad-container"]',
        'iframe[src*="doubleclick"]', 'iframe[src*="googlesyndication"]',
        
        // Social media widgets
        '[class*="social-share"]', '[class*="share-button"]',
        '[class*="social-icons"]',
        
        // Cookie banners and popups
        '[class*="cookie-banner"]', '[class*="cookie-consent"]',
        '[id*="cookie"]', '[class*="gdpr"]',
        
        // Chat widgets
        '[class*="chat-widget"]', '[id*="intercom"]',
        '[id*="drift-widget"]', '[class*="helpscout"]',
        
        // Navigation noise (headers/nav bars are huge DOM consumers)
        // Be careful: only exclude secondary/utility navs, not the main nav
        '[class*="utility-nav"]', '[class*="secondary-nav"]',
        '[class*="breadcrumb"]',
        
        // Decorative/cosmetic
        '[aria-hidden="true"]:not(button):not(a):not(input)',
        '[role="presentation"]',
        '[class*="skeleton"]', '[class*="shimmer"]',
        '[class*="placeholder-"]',
        
        // Off-screen / hidden menus (often huge)
        '[class*="offcanvas"]', '[class*="off-canvas"]',
    ];
    
    JUNK_SELECTORS.forEach(selector => {
        try {
            document.querySelectorAll(selector).forEach(el => {
                // Don't exclude elements that are interactive or contain interactive children
                const hasInteractive = el.querySelector('input, button, select, textarea, a[href]');
                if (!hasInteractive && !el.matches('input, button, select, textarea, a[href]')) {
                    el.setAttribute(EXCLUDE_ATTR, 'true');
                }
            });
        } catch(e) {} // Skip invalid selectors
    });

    // 3. Large hidden elements: anything display:none or visibility:hidden with lots of children
    document.querySelectorAll('*').forEach(el => {
        try {
            const style = window.getComputedStyle(el);
            if ((style.display === 'none' || style.visibility === 'hidden') && el.children.length > 3) {
                el.setAttribute(EXCLUDE_ATTR, 'true');
            }
        } catch(e) {}
    });

    // 4. Duplicate/repetitive elements: if there are >20 siblings with the same tag+class,
    //    mark all but the first 10 (common in product grids, search results, etc.)
    const containers = document.querySelectorAll('ul, ol, div[class], section');
    containers.forEach(container => {
        const children = Array.from(container.children);
        if (children.length > 20) {
            // Group by tag+class fingerprint
            const groups = {};
            children.forEach(child => {
                const key = child.tagName + '|' + (child.className || '');
                if (!groups[key]) groups[key] = [];
                groups[key].push(child);
            });
            Object.values(groups).forEach(group => {
                if (group.length > 15) {
                    // Keep first 10, mark rest for exclusion
                    group.slice(10).forEach(el => {
                        el.setAttribute(EXCLUDE_ATTR, 'true');
                    });
                }
            });
        }
    });
    
    return document.querySelectorAll('[' + EXCLUDE_ATTR + '="true"]').length;
})();
"""


async def inject_stealth(browser_session):
    """Inject stealth JavaScript into the browser session's current page.
    Call this AFTER the browser session has started/connected."""
    try:
        page = await browser_session.get_current_page()
        await page.add_init_script(STEALTH_JS)
        # Also evaluate immediately on current page
        await page.evaluate(STEALTH_JS)
        print("  [Stealth] Anti-detection scripts injected successfully.")
    except Exception as e:
        print(f"  [Stealth] Warning: injection failed (non-fatal): {e}")


async def cleanup_dom(browser_session):
    """Run DOM cleanup on the current page to exclude junk elements.
    Call this AFTER navigation to a page, before the agent reads the DOM.
    Returns the number of elements marked for exclusion."""
    try:
        page = await browser_session.get_current_page()
        excluded_count = await page.evaluate(DOM_CLEANUP_JS)
        print(f"  [DOM Cleanup] Marked {excluded_count} junk elements for exclusion.")
        return excluded_count
    except Exception as e:
        print(f"  [DOM Cleanup] Warning: cleanup failed (non-fatal): {e}")
        return 0
