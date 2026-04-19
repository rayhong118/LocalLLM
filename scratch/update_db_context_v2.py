import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SessionLocal, Context

db = SessionLocal()
try:
    ctx = db.query(Context).filter(Context.name == 'How to look for Safeway deals').first()
    if ctx:
        ctx.content = """### SAFEWAY DEALS WORKFLOW (V2)

PHASE 1: NAVIGATION
1. Use 'nav_to_url' -> https://www.safeway.com/loyalty/coupons-deals

PHASE 2: FILTERING (CRITICAL)
2. MANDATORY: Use `safeway_filter_category` skill.
3. Use category: "Ice Cream & Novelties" for any ice cream task.
4. If the category does not appear to apply, use `scroll(down=True)` and try again.

PHASE 3: INSPECTION
5. MANDATORY: Use `safeway_click_details` skill for every coupon.
6. Check 'Eligible Items' inside the popup.
7. FORBIDDEN: Do NOT use the built-in 'extract' tool.
8. FORBIDDEN: Do NOT use 'save_as_pdf' or 'screenshot' as they are disabled.

PHASE 4: REPORTING
9. Provide Name, Original Price, and Deal Price in the 'done' tool."""
        db.commit()
        print("Success: Context updated to V2.")
finally:
    db.close()
