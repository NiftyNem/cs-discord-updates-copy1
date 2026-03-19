import feedparser
import requests
import os
import re
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ==============================
# CONFIG
# ==============================
RSS_SOURCES = [
    "https://store.steampowered.com/feeds/news/app/730/",
    "https://blog.counter-strike.net/index.php/category/updates/feed/",
    "https://steamcommunity.com/games/730/rss/"
]

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
LAST_UPDATE_FILE = "last_update.txt"

# ==============================
# HTML CLEANER (ROBUST)
# ==============================
def clean_html(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")

    # ----------------------------
    # 1. Headers tipo Steam (.bb_h3)
    # ----------------------------
    for h in soup.select(".bb_h3"):
        h.replace_with(f"\n### {h.get_text(strip=True)}\n")

    # ----------------------------
    # 2. Links → Markdown
    # ----------------------------
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text and href:
            a.replace_with(f"[{text}]({href})")

    # ----------------------------
    # 3. Imágenes (solo 1)
    # ----------------------------
    images = soup.find_all("img")
    for i, img in enumerate(images):
        src = img.get("src", "")
        if i == 0 and src:
            img.replace_with(f"\n{src}\n")
        else:
            img.decompose()

    # ----------------------------
    # 4. Listas con indentación
    # ----------------------------
    def parse_list(ul, depth=0):
        lines = []
        for li in ul.find_all("li", recursive=False):
            prefix = "  " * depth + "- "
            text = li.get_text(" ", strip=True)

            sub_ul = li.find("ul")
            if sub_ul:
                sub_ul.extract()
                lines.append(prefix + text)
                lines.extend(parse_list(sub_ul, depth + 1))
            else:
                lines.append(prefix + text)

        return lines

    for ul in soup.find_all("ul"):
        lines = parse_list(ul)
        ul.replace_with("\n" + "\n".join(lines) + "\n")

    # ----------------------------
    # 5. Saltos de línea
    # ----------------------------
    for br in soup.find_all("br"):
        br.replace_with("\n")

    # ----------------------------
    # 6. Texto base
    # ----------------------------
    text = soup.get_text("\n")

    # ----------------------------
    # 7. Headers tipo [GAMEPLAY]
    # ----------------------------
    text = re.sub(
        r'\\?\[\s*(.*?)\s*\]',
        lambda m: f"\n### {m.group(1).title()}\n",
        text
    )

    # ----------------------------
    # 8. Limpiar espacios
    # ----------------------------
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

# ==============================
# STORAGE
# ==============================
def get_last_saved_id():
    if os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None

def save_last_id(entry_id):
    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        f.write(entry_id)

# ==============================
# DISCORD PAYLOAD
# ==============================
def build_payload(entry):
    content = ""
    if 'content' in entry:
        content = entry.content[0].value
    else:
        content = entry.get('summary', entry.get('description', ''))

    clean_content = clean_html(content)

    # Separador visual
    if clean_content:
        clean_content += "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Límite Discord (embed)
    if len(clean_content) > 4000:
        clean_content = clean_content[:3990] + "..."

    image_url = "https://cdn.akamai.steamstatic.com/steam/apps/730/capsule_617x353.jpg"
    current_date = datetime.now().strftime("%d/%m/%Y %H:%M")

    payload = {
        "embeds": [{
            "title": f"📰 {entry.title}",
            "description": clean_content,
            "url": entry.link,
            "color": 3092790,
            "image": {
                "url": image_url
            },
            "footer": {
                "text": f"Updated: {current_date}",
            }
        }],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "View Full Notes ↗️",
                        "url": entry.link
                    }
                ]
            }
        ]
    }

    return payload

# ==============================
# DISCORD SENDER
# ==============================
def send_to_discord(entry):
    payload = build_payload(entry)

    if not DISCORD_WEBHOOK_URL:
        print(json.dumps(payload, indent=4, ensure_ascii=False))
        return

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)

    if response.status_code in (200, 204):
        print("Message sent successfully.")
    else:
        print(f"Error {response.status_code}: {response.text}")

# ==============================
# MAIN
# ==============================
def main():
    feed = None

    for url in RSS_SOURCES:
        try:
            response = requests.get(
                url,
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=10
            )
            if response.status_code == 200:
                feed = feedparser.parse(response.content)
                if feed.entries:
                    break
        except Exception:
            continue

    if not feed or not feed.entries:
        return

    latest_entry = feed.entries[0]
    latest_id = getattr(latest_entry, 'id', latest_entry.link)
    last_id = get_last_saved_id()

    if latest_id != last_id or not DISCORD_WEBHOOK_URL:
        send_to_discord(latest_entry)
        if DISCORD_WEBHOOK_URL:
            save_last_id(latest_id)

if __name__ == "__main__":
    main()
