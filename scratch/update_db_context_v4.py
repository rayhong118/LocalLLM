import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SessionLocal, Context

db = SessionLocal()
try:
    ctx = db.query(Context).filter(Context.name == 'How to look for Safeway deals').first()
    if ctx:
        ctx.content = """### SAFEWAY DEALS WORKFLOW (V4)

PHASE 1: NAVIGATION
1. Use 'nav_to_url' -> https://www.safeway.com/loyalty/coupons-deals

PHASE 2: DISCOVERY & FILTERING
2. DISCOVERY: Before filtering, scroll down the sidebar to see the 'Category' list.
3. VISIBILITY: If the sidebar is missing, it may be collapsed or marked as navigation junk. Use 'scroll' or 'find_elements' to locate it.
4. MANDATORY: Use `safeway_filter_category` skill with a name discovered on the page (e.g., 'Frozen Foods').

PHASE 3: INSPECTION & BOT HANDLING
5. SECURITY: If 'Security Check' or 'Verify you are human' appears, WAIT 5 seconds, scroll, and try to continue.
6. INSPECTION: Use `safeway_click_details` skill for viewing individual coupon cards.
7. ELIGIBILITY: Check 'Eligible Items' inside the popup.

### OUTPUT REQUIREMENTS
List matching products individually with Name, Original Price, and Deal Price.

FORBIDDEN: Do NOT use the built-in 'extract' tool.
FORBIDDEN: Do NOT search for 'checkbox-state' manually."""
        db.commit()
        print("Success: Context updated to V4.")
finally:
    db.close()
