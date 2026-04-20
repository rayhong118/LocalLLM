import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SessionLocal, Context

db = SessionLocal()
try:
    ctx = db.query(Context).filter(Context.name == 'How to look for Safeway deals').first()
    if ctx:
        ctx.content = """### SAFEWAY DEALS WORKFLOW (V3 - AGNOSTIC)

PHASE 1: NAVIGATION
1. Use 'nav_to_url' -> https://www.safeway.com/loyalty/coupons-deals

PHASE 2: DISCOVERY & FILTERING
2. DISCOVERY: Before filtering, scroll down the sidebar or use 'search_page' to see which categories (e.g., 'Frozen', 'Dairy', 'Beverages') are available.
3. MANDATORY: Use `safeway_filter_category` skill with a valid category name discovered on the page.
4. If the category does not appear to apply, use `scroll(down=True)` and look again.

PHASE 3: INSPECTION
5. MANDATORY: Use `safeway_click_details` skill for viewing individual coupon cards.
6. Provide a brand or keyword from the coupon title to the skill.
7. ELIGIBILITY: Check 'Eligible Items' inside the popup to verify matches.

PHASE 4: REPORTING
8. Provide Name, Original Price, and Deal Price in the 'done' tool for each match found.

FORBIDDEN: Do NOT use the built-in 'extract' tool.
FORBIDDEN: Do NOT search for 'checkbox-state' manually."""
        db.commit()
        print("Success: Context updated to V3 (Product Agnostic).")
finally:
    db.close()
