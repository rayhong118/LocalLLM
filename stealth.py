# stealth.py — Inject anti-detection JavaScript and DOM cleanup into browser pages
"""
Two-part injection for Playwright browser sessions:
1. Stealth: Patches navigator.webdriver, chrome.runtime, and other bot fingerprint leaks.
2. DOM Cleanup: Marks non-essential DOM elements with data-browser-use-exclude="true"
   so browser-use's serializer skips them, drastically reducing context token usage.
"""

STEALTH_JS = """
(function() {
    try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' }
            ]
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        if (!window.chrome) { window.chrome = {}; }
        if (!window.chrome.runtime) {
            window.chrome.runtime = { connect: () => {}, sendMessage: () => {} };
        }
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) => (
            p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : originalQuery(p)
        );
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        // Mask WebGL renderer
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Open Source Technology Center';
            if (parameter === 37446) return 'Mesa DRI Intel(R) UHD Graphics (CML GT2)';
            return getParameter.apply(this, arguments);
        };

    } catch(e) {}
})();
"""

DOM_CLEANUP_JS = """
(function() {
    const attr = 'data-browser-use-exclude';
    const tags = ['noscript', 'svg', 'path', 'style', 'script', 'map'];
    tags.forEach(t => {
        document.querySelectorAll(t).forEach(el => el.setAttribute(attr, 'true'));
    });
    
    const items = [
        '[id*="google_ads"]', 'iframe', '[class*="cookie"]', '[class*="modal-dialog"]', 
        '[class*="overlay"]', '[style*="display: none"]', '[hidden]',
        'footer', 'header', '.footer', '.header', '#footer', '#header',
        '.skip-link', '.nav-menu', '.top-nav', '.chatbot', '#customer-support',
        '.marketing-banner', '.promo-strip', '.newsletter-signup'
    ];
    items.forEach(s => {
        try {
            document.querySelectorAll(s).forEach(el => {
                el.setAttribute(attr, 'true');
                // Also hide physically to ensure it doesn't interfere with interaction
                el.style.display = 'none';
            });
        } catch(e) {}
    });
    
    return document.querySelectorAll('[' + attr + '="true"]').length;
})();
"""

async def inject_stealth(browser_session):
    try:
        page = await browser_session.get_current_page()
        # add_init_script is enough for all future navigations
        await page.add_init_script(STEALTH_JS)
    except Exception:
        pass

async def cleanup_dom(browser_session):
    try:
        page = await browser_session.get_current_page()
        count = await page.evaluate(DOM_CLEANUP_JS)
        return count
    except Exception as e:
        # Don't log syntax errors to console, just fail silently as it's non-critical
        return 0
