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
    try {
        const attr = 'data-browser-use-exclude';
        const tags = ['noscript', 'svg', 'path', 'style', 'script', 'map'];
        tags.forEach(t => document.querySelectorAll(t).forEach(el => el.setAttribute(attr, 'true')));
        
        const selectors = [
            '[class*="ad-"]', '[class*="ads-"]', '[class*="advert"]', '[id*="google_ads"]',
            'iframe', '[class*="social-"]', '[class*="share-"]', '[class*="cookie"]',
            '[class*="chat"]', '[aria-hidden="true"]:not(button):not(input)', '[role="presentation"]',
            '[class*="modal-dialog"]', '[class*="overlay"]'
        ];
        selectors.forEach(s => document.querySelectorAll(s).forEach(el => el.setAttribute(attr, 'true')));
        
        document.querySelectorAll('[style*="display: none"], [style*="visibility: hidden"], [hidden]').forEach(el => {
            el.setAttribute(attr, 'true');
        });
        
        return document.querySelectorAll('[' + attr + '="true"]').length;
    } catch(e) { return -1; }
})();
"""

async def inject_stealth(browser_session):
    try:
        page = await browser_session.get_current_page()
        await page.add_init_script(STEALTH_JS)
        await page.evaluate(STEALTH_JS)
    except Exception:
        pass

async def cleanup_dom(browser_session):
    try:
        page = await browser_session.get_current_page()
        # Wait a tiny bit for SPAs to settle if needed, but usually on_new_step is enough
        count = await page.evaluate(DOM_CLEANUP_JS)
        if count > 0:
            print(f"  [DOM Cleanup] Excluded {count} elements.")
        return count
    except Exception as e:
        print(f"  [DOM Cleanup] Failed: {e}")
        return 0
