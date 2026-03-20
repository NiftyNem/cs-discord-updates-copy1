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
    "https://store.steampowered.com/feeds/news/app/730/"
]

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
LAST_UPDATE_FILE = "last_update.txt"

# ==============================
# HTML CLEANER
# ==============================
def clean_html(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")

    # Headers Steam
    for h in soup.select(".bb_h3"):
        h.replace_with(f"\n### {h.get_text(strip=True)}\n")

    # Links → Markdown
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text and href:
            a.replace_with(f"[{text}]({href})")

    # Imágenes (solo 1)
    images = soup.find_all("img")
    for i, img in enumerate(images):
        src = img.get("src", "")
        if i == 0 and src:
            img.replace_with(f"\n{src}\n")
        else:
            img.decompose()

    # Listas
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

    # Saltos de línea
    for br in soup.find_all("br"):
        br.replace_with("\n")

    # Texto base
    text = soup.get_text("\n")

    # Headers tipo [GAMEPLAY]
    text = re.sub(
        r'\\?\[\s*(.*?)\s*\]',
        lambda m: f"\n### {m.group(1).title()}\n",
        text
    )

    # Limpieza
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

# ==============================
# STORAGE (MULTI-ID)
# ==============================
def get_saved_ids():
    if os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
            return set(f.read().splitlines())
    return set()

def save_id(entry_id):
    ids = get_saved_ids()
    ids.add(entry_id)

    # Mantener últimos 20
    ids = list(ids)[-20:]

    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(ids))

# ==============================
# ID NORMALIZADO
# ==============================
def get_entry_id(entry):
    link = entry.link.strip()
    link = re.sub(r'\?.*$', '', link)
    return link

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

    if clean_content:
        clean_content += "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if len(clean_content) > 4000:
        clean_content = clean_content[:3990] + "..."

    image_url = "https://cdn.akamai.steamstatic.com/steam/apps/730/capsule_617x353.jpg"
    current_date = datetime.now().strftime("%d/%m/%Y %H:%M")

    return {
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
# MAIN (FIXED)
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

    saved_ids = get_saved_ids()

    # Ordenar por fecha (clave)
    entries = sorted(
        feed.entries,
        key=lambda x: x.published_parsed,
        reverse=True
    )

    for entry in entries:
        entry_id = get_entry_id(entry)

        if entry_id not in saved_ids:
            send_to_discord(entry)
            save_id(entry_id)
            break  # Solo uno por ejecución

if __name__ == "__main__":
    main()
