import os
import re
import json
import html
import sys
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PRICES_FILE = "prices.json"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram preview]: {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if not resp.ok:
            print(f"Telegram error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Telegram request failed: {e}")


MIN_PRICE = 10.0
MIN_PRICE_FALLBACK = 30.0  # higher floor for fallback to filter "save $X" elements


def _debug_page(page, tag):
    safe = tag.replace(" ", "_").replace("/", "_")
    page.screenshot(path=f"debug_{safe}.png")
    html = page.content()
    with open(f"debug_{safe}.html", "w") as f:
        f.write(html)
    print(f"  Debug files saved: debug_{safe}.png / .html")


def scrape_hotel_price(hotel):
    url = hotel["url"]
    room_name = hotel["room_name"]
    print(f"Scraping {hotel['name']} — looking for room: {room_name}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "en-SG,en;q=0.9",
            }
        )
        page = context.new_page()

        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Wait for page content to render (Trip.com is a heavy JS app)
        page.wait_for_timeout(5000)
        # Try waiting for common content indicators
        try:
            page.wait_for_selector('[class*="room"], [class*="price"], [class*="RatePlan"]', timeout=15000)
        except:
            pass
        page.wait_for_timeout(5000)

        current_url = page.url
        if "signin" in current_url.lower() or "login" in current_url.lower():
            _debug_page(page, "login_redirect")
            raise Exception("Redirected to login — anti-bot triggered")

        target_price = None
        currency = ""

        # Strategy 1: find room name in page, then find its room card and extract lowest price
        try:
            result = page.evaluate("""(data) => {
                const {roomName, minPrice} = data;
                const lowerName = roomName.toLowerCase();

                const currencyMap = {
                    '$': 'USD', 'S$': 'SGD', 'HK$': 'HKD', 'NT$': 'TWD',
                    '\\u00a5': 'JPY', '\\u20ac': 'EUR', '\\u00a3': 'GBP',
                    'A$': 'AUD', 'C$': 'CAD', '\\u20b9': 'INR',
                    'RM': 'MYR', '\\u20ab': 'VND', '\\u0e3f': 'THB',
                    '\\u20a9': 'KRW', '\\u00a5': 'CNY',
                };

                const normalizeCurrency = (sym) => {
                    if (!sym) return 'SGD';
                    if (sym in currencyMap) return currencyMap[sym];
                    const upper = sym.toUpperCase();
                    if (upper.startsWith('S$')) return 'SGD';
                    if (upper.startsWith('HK$')) return 'HKD';
                    if (upper.startsWith('NT$')) return 'TWD';
                    if (upper.startsWith('A$')) return 'AUD';
                    if (upper.startsWith('C$')) return 'CAD';
                    if (upper.endsWith('$')) return 'USD';
                    if (upper === 'SGD' || upper === 'USD' || upper === 'EUR' || upper === 'GBP') return upper;
                    return 'SGD';
                };

                const excludePriceKw = ['breakfast', 'optional', 'add-on', 'add on', 'supplement', 'extra'];

                const parsePriceAny = (text) => {
                    if (!text) return null;
                    const matches = [...text.matchAll(/(?:S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})\\s*[\\d,]{2,}\\.?\\d*/g)];
                    const results = [];
                    const seen = new Set();
                    for (const m of matches) {
                        const raw = m[0].trim();
                        const sym = raw.match(/(S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})/)?.[0] || '';
                        const numStr = raw.replace(sym, '').trim().replace(/,/g, '');
                        const num = parseFloat(numStr);
                        if (num >= minPrice && !seen.has(num)) {
                            seen.add(num);
                            results.push({ price: num, currency: normalizeCurrency(sym.trim()) });
                        }
                    }
                    return results.length > 0 ? results : null;
                };

                const getDepth = (el) => {
                    let d = 0;
                    while (el) { el = el.parentElement; d++; }
                    return d;
                };

                // Find all leaf elements containing the room name
                const all = document.querySelectorAll('*');
                const nameElements = [];
                for (const el of all) {
                    if (el.children.length > 0) continue;
                    if (!el.textContent) continue;
                    const t = el.textContent.trim();
                    if (t.length > 0 && t.length < 300 && t.toLowerCase().includes(lowerName)) {
                        nameElements.push(el);
                    }
                }

                if (nameElements.length === 0) {
                    for (const el of all) {
                        if (el.children.length > 3) continue;
                        if (!el.textContent) continue;
                        const t = el.textContent.trim();
                        if (t.length > 0 && t.length < 300 && t.toLowerCase().includes(lowerName)) {
                            nameElements.push(el);
                        }
                    }
                }

                nameElements.sort((a, b) => getDepth(b) - getDepth(a));
                if (nameElements.length === 0) return null;

                // Walk up from the most specific name element to find the room card container
                const findRoomCard = (el) => {
                    let card = el.parentElement;
                    let safety = 0;
                    while (card && safety < 10) {
                        const cls = (card.className || '').toLowerCase();
                        if (cls.includes('roomcard') || cls.includes('room-card') || cls.includes('roomcard')) break;
                        card = card.parentElement;
                        safety++;
                    }
                    // If no room card container found, use parent chain (up to 6 levels)
                    if (!card || safety >= 10) {
                        card = el;
                        for (let i = 0; i < 6; i++) {
                            if (card.parentElement) card = card.parentElement;
                        }
                    }
                    return card;
                };

                // Process each matching name element from most specific
                for (const el of nameElements) {
                    const card = findRoomCard(el);
                    const allPrices = [];

                    const walkCollect = (el, depth) => {
                        if (!el || depth > 12) return;
                        const t = (el.textContent || '').trim();
                        if (t.length > 0 && t.length < 120) {
                            const prices = parsePriceAny(t);
                            if (prices) {
                                const hasExclude = excludePriceKw.some(kw => t.toLowerCase().includes(kw));
                                if (!hasExclude) {
                                    for (const p of prices) {
                                        allPrices.push({
                                            ...p,
                                            element: el.tagName,
                                            class: (el.className || '').substring(0, 50),
                                            text: t.substring(0, 60),
                                            depth: depth
                                        });
                                    }
                                }
                            }
                        }
                        for (const c of el.children) {
                            walkCollect(c, depth + 1);
                        }
                    };

                    walkCollect(card, 0);

                    if (allPrices.length > 0) {
                        allPrices.sort((a, b) => a.price - b.price);
                        const best = allPrices[0];
                        return { price: best.price, currency: best.currency, method: 'lowest_in_card', debug: `${best.element}:${best.depth}` };
                    }
                }

                return null;
            }""", {"roomName": room_name, "minPrice": MIN_PRICE})

            if result:
                target_price = result["price"]
                currency = result["currency"]
                print(f"  Found by JS traversal: {currency}{target_price} ({result['method']}, {result.get('debug','')})")
        except Exception as e:
            print(f"  JS eval error: {e}")

        # Strategy 2: broad card + price scraping with filtering
        if target_price is None:
            print("  JS element traversal failed, trying CSS card + price selectors")
            page.wait_for_timeout(3000)

            card_selectors = [
                '[class*="roomCard"]', '[class*="room-card"]',
                '[class*="roomlist"]', '[class*="room-list"]',
                '[class*="roomList"]', '[class*="SelectRoom"]',
                '[class*="select-room"]', '[data-testid*="room"]',
                '[class*="RoomList"]', '[class*="room_item"]',
                '[class*="roomItem"]', '[class*="ratePlan"]',
                '[class*="rate-plan"]', '[class*="RatePlan"]',
            ]
            all_cards = []
            for sel in card_selectors:
                cards = page.query_selector_all(sel)
                if cards:
                    all_cards = cards
                    print(f"  Found {len(cards)} cards with selector: {sel}")
                    break

            price_inner_selectors = [
                'span[class*="realPrice"]', '[class*="realPrice"]',
                '[class*="totalPrice"]', '[class*="roomPrice"]',
                '[class*="memberPrice"]', '[class*="finalPrice"]',
                '[class*="price"]', '[class*="Price"]',
            ]

            if all_cards:
                for card_idx, card in enumerate(all_cards):
                    card_text = card.inner_text()
                    if room_name.lower() in card_text.lower():
                        print(f"  Room name found in card! Full text preview: {card_text[:200]}")
                        # Use JS to find the specific room element and its price
                        js_result = page.evaluate("""(data) => {
                            const {roomName, minPrice, cardIndex} = data;
                            const cards = document.querySelectorAll('[class*="RoomList"], [class*="roomList"], [class*="roomlist"]');
                            if (!cards[cardIndex]) return null;
                            const card = cards[cardIndex];

                            const parsePrice = (text) => {
                                const m = text.match(/[A-Z$\\u00a5\\u20ac\\u00a3]?\\s*[\\d,]{2,}\\.?\\d*/);
                                if (!m) return null;
                                const raw = m[0].trim();
                                const cur = (raw.match(/[A-Z$\\u00a5\\u20ac\\u00a3]+/) || [])[0] || '';
                                const num = parseFloat((raw.match(/[\\d,]{2,}\\.?\\d*/) || ['0'])[0].replace(',', ''));
                                if (num >= minPrice) return { price: num, currency: cur || 'SGD' };
                                return null;
                            };

                            const walkDown = (el, depth) => {
                                if (!el || depth > 8) return null;
                                const r = parsePrice(el.textContent || '');
                                if (r) return r;
                                for (const child of el.children) {
                                    const res = walkDown(child, depth + 1);
                                    if (res) return res;
                                }
                                return null;
                            };

                            const walkBothWays = (el) => {
                                let parent = el.parentElement;
                                let d = 0;
                                while (parent && d < 10) {
                                    for (const child of parent.children) {
                                        const r = walkDown(child, 0);
                                        if (r) return r;
                                    }
                                    parent = parent.parentElement;
                                    d++;
                                }
                                return null;
                            };

                            // Find room name element within the card
                            const all = card.querySelectorAll('*');
                            const lowerName = roomName.toLowerCase();
                            const candidates = [];
                            for (const el of all) {
                                if (!el.textContent) continue;
                                const t = el.textContent.trim();
                                if (t.length > 0 && t.length < 300 && t.toLowerCase().includes(lowerName)) {
                                    candidates.push(el);
                                }
                            }

                            // Try candidate elements from deepest (most specific) first
                            candidates.sort((a, b) => b.textContent.length - a.textContent.length);
                            for (const el of candidates) {
                                const r = parsePrice(el.textContent || '');
                                if (r) return r;
                                const res = walkBothWays(el);
                                if (res) return res;
                            }
                            return null;
                        }""", {"roomName": room_name, "minPrice": MIN_PRICE, "cardIndex": card_idx})

                        if js_result:
                            target_price = js_result["price"]
                            currency = js_result["currency"]
                            print(f"  Found price near room title: {currency}{target_price}")
                        else:
                            # Fallback: just use first price in the card
                            for ps in price_inner_selectors:
                                price_el = card.query_selector(ps)
                                if price_el:
                                    raw = price_el.inner_text().strip()
                                    curr_m = re.search(r'[A-Z$¥€£]+', raw)
                                    num_m = re.search(r'[\d,]+\.?\d*', raw.replace(",", ""))
                                    if num_m:
                                        val = float(num_m.group())
                                        if val >= MIN_PRICE:
                                            target_price = val
                                            currency = (curr_m or [""]).group() if curr_m else ""
                                            print(f"  Found price in room card (fallback): {currency}{target_price}")
                                            break
                        if target_price:
                            break

        # Strategy 3: fallback – any price >= MIN_PRICE_FALLBACK on page
        if target_price is None:
            _debug_page(page, f"room_not_found_{hotel['name']}")
            print(f"  Room name not found in any card, scraping all visible prices >= ${MIN_PRICE_FALLBACK}")

            price_selectors_broad = [
                '[class*="realPrice"]', '[class*="totalPrice"]',
                '[class*="roomPrice"]', '[class*="memberPrice"]',
                '[class*="finalPrice"]', '[class*="price"]',
                '[class*="Price"]',
            ]
            candidates = []
            for ps in price_selectors_broad:
                els = page.query_selector_all(ps)
                for el in els:
                    raw = el.inner_text().strip()
                    # Skip if text contains discount/add-on labels, not prices
                    if re.search(r'save|off|tax|fee|breakfast|optional', raw, re.IGNORECASE):
                        continue
                    num_m = re.search(r'[\d,]+\.?\d*', raw.replace(",", ""))
                    if num_m:
                        val = float(num_m.group())
                        if val >= MIN_PRICE_FALLBACK:
                            curr_m = re.search(r'[A-Z$¥€£]+', raw)
                            cur = (curr_m or [""]).group() if curr_m else ""
                            candidates.append((val, cur))
                            if not currency:
                                currency = cur

            prices_only = [c[0] for c in candidates]
            if prices_only:
                target_price = min(prices_only)
                currency = next((c[1] for c in candidates if c[0] == target_price), currency or "SGD")
                print(f"  Fallback: {len(candidates)} candidates, min={currency}{target_price}")

        browser.close()

        if target_price is None:
            _debug_page(page, f"no_price_{hotel['name']}")
            raise Exception(f"Could not find any price >= ${MIN_PRICE_FALLBACK}")

        return target_price, currency


def main():
    if not os.path.exists(PRICES_FILE):
        print(f"Error: {PRICES_FILE} not found")
        sys.exit(1)

    with open(PRICES_FILE) as f:
        hotels = json.load(f)

    changed = False
    for hotel in hotels:
        try:
            new_price, currency = scrape_hotel_price(hotel)
        except Exception as e:
            msg = f"⚠️ Error: {hotel['name']} — {html.escape(str(e))}"
            print(msg)
            send_telegram(msg)
            continue

        old_price = hotel["last_price"]
        hotel["currency"] = currency or "SGD"

        old_str = f"{old_price:.2f}" if old_price is not None else "N/A"
        print(f"  Result: {hotel['currency']}{new_price:.2f} (was {old_str})")

        if old_price is None or old_price <= 0:
            hotel["last_price"] = new_price
            changed = True
            send_telegram(
                f"🔍 <b>Price Baseline Set</b>\n"
                f"🏨 {hotel['name']}\n"
                f"🛏️ {hotel['room_name']}\n"
                f"💵 {hotel['currency']}{new_price:.2f}\n"
                f"🔗 <a href='{hotel['url']}'>View on Trip.com</a>"
            )
            print(f"  Initial price set: {hotel['currency']}{new_price:.2f}")
        elif new_price < old_price:
            drop_pct = (old_price - new_price) / old_price * 100
            send_telegram(
                f"💰 <b>Price Drop!</b>\n"
                f"🏨 {hotel['name']}\n"
                f"🛏️ {hotel['room_name']}\n"
                f"📉 {hotel['currency']}{old_price:.2f} → {hotel['currency']}{new_price:.2f}\n"
                f"💵 Save: {hotel['currency']}{old_price - new_price:.2f} ({drop_pct:.1f}%)\n"
                f"🔗 <a href='{hotel['url']}'>View on Trip.com</a>"
            )
            hotel["last_price"] = new_price
            changed = True
        elif new_price > old_price:
            print(f"  Price up to {hotel['currency']}{new_price:.2f}, ignoring (stored low: {hotel['currency']}{old_price:.2f})")
        else:
            print(f"  Price unchanged: {hotel['currency']}{new_price:.2f}")

    if changed:
        with open(PRICES_FILE, "w") as f:
            json.dump(hotels, f, indent=2)
        print("prices.json saved")
    else:
        print("No changes to save")


if __name__ == "__main__":
    main()
