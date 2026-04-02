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
    # 1. Pre-procesado: Steam escapa los corchetes \[ ] en sus notas
    raw_html = raw_html.replace(r'\[', '[').replace(r'\]', ']')
    
    soup = BeautifulSoup(raw_html, "html.parser")

    # Headers Steam (.bb_h3)
    for h in soup.select(".bb_h3"):
        if h.parent:
            h.replace_with(f"\n### {h.get_text(strip=True)}\n")

    # Links → Markdown
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text and href and a.parent:
            replacement = f"[{text}]({href})" if text != href else href
            a.replace_with(replacement)

    # Images (solo la primera para el cuerpo)
    images = soup.find_all("img")
    for i, img in enumerate(images):
        src = img.get("src", "")
        if i == 0 and src and img.parent:
            img.replace_with(f"\n{src}\n")
        else:
            img.decompose()

    # Listas (UL / LI) - Procesamos de abajo hacia arriba para evitar el ValueError
    for ul in reversed(soup.find_all("ul")):
        if not ul.parent:
            continue
            
        # Si es una lista anidada, dejamos que el UL padre la maneje recursivamente
        if ul.parent.name == "li":
            continue

        lines = []
        for li in ul.find_all("li"):
            # Indentación según profundidad
            depth = len(li.find_parents("ul")) - 1
            indent = "  " * depth
            lines.append(f"{indent}• {li.get_text(strip=True)}")
        
        ul.replace_with("\n" + "\n".join(lines) + "\n")

    # Saltos de línea y Párrafos
    for br in soup.find_all("br"):
        if br.parent: br.replace_with("\n")
            
    for p in soup.find_all(["p", "div"]):
        if p.parent:
            p.insert_before("\n")
            p.insert_after("\n")
            p.unwrap()

    # Extraer texto plano
    text = soup.get_text()

    # --- POST-PROCESADO ---
    
    # Convertir secciones [ EJEMPLO ] a negrita para Discord
    text = re.sub(
        r'\[\s*([A-Z0-9\s\-_]+)\s*\]',
        lambda m: f"\n**{m.group(1).strip()}**",
        text
    )

    # Cleanup de espacios y líneas
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' , ', ', ', text) 
    text = re.sub(r' +', ' ', text)

    return text.strip()

# ==============================
# STORAGE
# ==============================
def get_last_id():
    if os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None

def save_last_id(entry_id):
    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        f.write(entry_id)

# ==============================
# ID NORMALIZATION
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

    # Límite de Embed de Discord (4096 caracteres)
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
                "text": f"Actualizado: {current_date}",
            }
        }],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Ver notas completas ↗️",
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
        print("WEBHOOK no configurado. Mostrando payload en consola:")
        print(json.dumps(payload, indent=4, ensure_ascii=False))
        return

    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)

    if response.status_code in (200, 204):
        print(f"Enviado: {entry.title}")
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
        except Exception as e:
            print(f"Error cargando feed: {e}")
            continue

    if not feed or not feed.entries:
        print("No se pudo obtener el feed o está vacío.")
        return

    last_id = get_last_id()

    # Ordenar por fecha (más antiguo primero para enviar en orden)
    entries = sorted(
        feed.entries,
        key=lambda x: x.published_parsed if hasattr(x, 'published_parsed') else 0
    )

    new_entries = []
    found_last = last_id is None

    for entry in entries:
        entry_id = get_entry_id(entry)

        if entry_id == last_id:
            found_last = True
            continue

        if not found_last:
            continue

        new_entries.append(entry)

    if not new_entries:
        print("No hay actualizaciones nuevas.")
        return

    # Enviar nuevas entradas
    for entry in new_entries:
        send_to_discord(entry)

    # Guardar el último ID procesado satisfactoriamente
    latest_id = get_entry_id(new_entries[-1])
    save_last_id(latest_id)

if __name__ == "__main__":
    main()
