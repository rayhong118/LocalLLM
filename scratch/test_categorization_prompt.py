import httpx
import json
import asyncio

async def main():
    available_categories = [
        'Special Offers', 'Baby Care', 'Beverages', 'Bread & Bakery', 'Breakfast & Cereal',
        'Canned Goods & Soups', 'Condiments, Spices & Bake', 'Cookies, Snacks & Candy',
        'Dairy, Eggs & Cheese', 'Deli', 'Flowers & Decor', 'Frozen Foods', 'Fruits & Vegetables',
        'Grains, Pasta & Sides', 'International Cuisine', 'Meat & Seafood', 'Paper, Cleaning & Home',
        'Personal Care & Health', 'Pet Care', 'Exclusive Brands', 'Gift Cards', 'Pick Up & Delivery',
        'Save on P&G Products', 'Weekly Ad Coupons', 'forU Save Days', 'Coupon', 'Personalized Deal'
    ]
    
    items = ['ice cream', 'coke', 'Arizona tea', 'steak', 'apples', 'paper tissues', 'sparkling water']
    
    selector_system = (
        "You are a categorization assistant for a grocery store. You must output ONLY a valid JSON object.\n"
        "Format: {\"mapping\": [{\"item\": \"item name\", \"category\": \"Category Name\"}, ...]}\n"
        "RULES:\n"
        "1. NEVER pick 'Special Offers' if a specific food category is available.\n"
        "2. If nothing fits, use 'NONE' as category.\n"
        "3. Crucially, items that are typically sold frozen (e.g., ice cream, frozen meals, frozen pizza, popsicles, frozen waffles, frozen vegetables, frozen fruit) MUST be categorized under 'Frozen Foods' (or similar frozen category) rather than their ingredient-based category (like 'Dairy', 'Meat', or 'Produce').\n"
        f"Available Categories: {', '.join(available_categories)}"
    )

    try:
        print("Sending request to local Ollama...")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "qwen3.5:9b",
                    "messages": [{"role": "user", "content": f"{selector_system}\n\nItems: {', '.join(items)}"}],
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {"temperature": 0, "num_ctx": 4096}
                },
                timeout=300
            )
            print("Full response:")
            print(resp.json())
            raw_content = resp.json().get("message", {}).get("content", "{}")
            print("Response raw content:")
            print(raw_content)
            data = json.loads(raw_content)
            print("\nParsed mapping:")
            for m in data.get("mapping", []):
                print(f"  - Item: '{m.get('item')}' -> Identified Category: '{m.get('category')}'")
    except Exception as e:
        import traceback
        print(f"Error calling Ollama: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
