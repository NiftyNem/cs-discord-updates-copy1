"""CS2 Update Notifier - Monitors Steam RSS feeds and sends patch notes to Discord."""

import logging
import os
import re
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree

import feedparser
import requests
from bs4 import BeautifulSoup, Tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STEAM_ICON_URL = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/logo.png"
DEFAULT_IMAGE_URL = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/header.jpg"
DISCORD_EMBED_MAX = 4096
DISCORD_EMBED_SAFE_MAX = 4000


@dataclass
class Config:
    webhooks: list[str] = field(default_factory=lambda: [])
    ping_role_id: Optional[str] = None
    rss_sources: list[str] = field(
        default_factory=lambda: [
            "https://store.steampowered.com/feeds/news/app/730/",
        ]
    )
    last_update_file: str = "last_update.txt"
    http_timeout: int = 15
    http_retries: int = 3

    @classmethod
    def from_env(cls) -> "Config":
        webhook_url = os.getenv("DISCORD_WEBHOOK", "")
        webhook_urls = os.getenv("DISCORD_WEBHOOKS", "")
        webhooks = []
        if webhook_url:
            webhooks.append(webhook_url)
        if webhook_urls:
            for url in webhook_urls.split(","):
                url = url.strip()
                if url and url not in webhooks:
                    webhooks.append(url)

        return cls(
            webhooks=webhooks,
            ping_role_id=os.getenv("DISCORD_PING_ROLE_ID") or None,
        )


def fetch_feed(url: str, timeout: int = 15, retries: int = 3) -> Optional[feedparser.FeedParserDict]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CS2-Update-Notifier/2.0"}
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if feed.entries:
                return feed
            logger.warning("Feed vacío en intento %d: %s", attempt, url)
        except requests.RequestException as e:
            logger.warning("Error en intento %d cargando %s: %s", attempt, url, e)
        if attempt < retries:
            time.sleep(2**attempt)
    return None


def extract_first_image(raw_html: str) -> Optional[str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    img = soup.find("img")
    if img and isinstance(img, Tag):
        src = img.get("src", "").strip()
        if src:
            return src
    return None


def clean_html(raw_html: str) -> str:
    raw_html = raw_html.replace(r"\[", "[").replace(r"\]", "]")
    soup = BeautifulSoup(raw_html, "html.parser")

    for h in soup.select(".bb_h3"):
        if h.parent:
            h.replace_with(f"\n### {h.get_text(strip=True)}\n")

    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text and href and a.parent:
            a.replace_with(f"[{text}]({href})" if text != href else href)

    for img in soup.find_all("img"):
        img.decompose()

    for ul in reversed(soup.find_all("ul")):
        if not ul.parent or ul.parent.name == "li":
            continue
        lines = []
        for li in ul.find_all("li"):
            depth = len(li.find_parents("ul")) - 1
            indent = "  " * depth
            lines.append(f"{indent}• {li.get_text(strip=True)}")
        ul.replace_with("\n" + "\n".join(lines) + "\n")

    for br in soup.find_all("br"):
        if br.parent:
            br.replace_with("\n")

    for p in soup.find_all(["p", "div"]):
        if p.parent:
            p.insert_before("\n")
            p.insert_after("\n")
            p.unwrap()

    text = soup.get_text()

    text = re.sub(
        r"\[\s*([A-Z0-9\s\-_]+)\s*\]",
        lambda m: f"\n**{m.group(1).strip()}**",
        text,
    )

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" , ", ", ", text)
    text = re.sub(r" +", " ", text)

    return text.strip()


def get_entry_id(entry: feedparser.FeedParserDict) -> str:
    link = getattr(entry, "link", "").strip()
    return re.sub(r"\?.*$", "", link)


def get_last_id(filepath: str) -> Optional[str]:
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


def save_last_id(filepath: str, entry_id: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(entry_id)


def build_embed_content(entry: feedparser.FeedParserDict) -> tuple[str, Optional[str]]:
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].value
    else:
        raw = entry.get("summary", entry.get("description", ""))

    image_url = extract_first_image(raw) or DEFAULT_IMAGE_URL
    clean = clean_html(raw)

    if clean:
        clean += "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if len(clean) > DISCORD_EMBED_SAFE_MAX:
        clean = clean[: DISCORD_EMBED_SAFE_MAX - 3] + "..."

    return clean, image_url


def build_payload(entry: feedparser.FeedParserDict, config: Config) -> dict:
    description, image_url = build_embed_content(entry)

    published_ts = None
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            published_ts = int(dt.timestamp())
        except (ValueError, TypeError):
            pass

    footer_text = "Fuente: Steam Community"
    if published_ts:
        footer_text += f" • <t:{published_ts}:R>"

    embed = {
        "author": {
            "name": "Steam News",
            "url": entry.link,
            "icon_url": STEAM_ICON_URL,
        },
        "title": entry.title,
        "description": description,
        "url": entry.link,
        "color": 0x2F3136,
        "thumbnail": {
            "url": image_url,
        },
        "footer": {
            "text": footer_text,
        },
    }

    if published_ts:
        embed["timestamp"] = datetime.fromtimestamp(published_ts, tz=timezone.utc).isoformat()

    content = ""
    if config.ping_role_id:
        content = f"<@&{config.ping_role_id}> "

    return {
        "content": content,
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Ver notas completas",
                        "url": entry.link,
                    }
                ],
            }
        ],
    }


def send_to_discord(entry: feedparser.FeedParserDict, webhook_url: str, config: Config) -> bool:
    payload = build_payload(entry, config)
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("Enviado: %s", entry.title)
            return True
        logger.error("Error %d: %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.error("Error enviando a Discord: %s", e)
    return False


def main() -> None:
    config = Config.from_env()

    if not config.webhooks:
        logger.warning("No se configuró DISCORD_WEBHOOK ni DISCORD_WEBHOOKS")
        return

    feed = None
    for url in config.rss_sources:
        feed = fetch_feed(url, timeout=config.http_timeout, retries=config.http_retries)
        if feed and feed.entries:
            break

    if not feed or not feed.entries:
        logger.error("No se pudo obtener el feed o está vacío")
        return

    last_id = get_last_id(config.last_update_file)

    entries = sorted(
        feed.entries,
        key=lambda x: x.published_parsed if hasattr(x, "published_parsed") else 0,
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
        logger.info("No hay actualizaciones nuevas")
        return

    logger.info("Se encontraron %d actualización(es) nueva(s)", len(new_entries))

    all_sent = True
    for entry in new_entries:
        for webhook_url in config.webhooks:
            success = send_to_discord(entry, webhook_url, config)
            if not success:
                all_sent = False
        if not all_sent:
            time.sleep(1)

    if all_sent:
        latest_id = get_entry_id(new_entries[-1])
        save_last_id(config.last_update_file, latest_id)
        logger.info("Último ID guardado: %s", latest_id)


if __name__ == "__main__":
    main()
