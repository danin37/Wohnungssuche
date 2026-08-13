"""
Scraper für ohne-makler.net.

WICHTIGER HINWEIS ZUR ZUVERLÄSSIGKEIT (bitte beim ersten Testlauf beachten):
Die genauen CSS-Klassen der Seite waren beim Bau dieses Skripts nicht
einsehbar (nur aufbereiteter Text, kein Roh-HTML). Die Extraktion arbeitet
daher primär über robuste Regex-Muster auf den Objekt-Links
(/immobilie/<ID>/) und den umgebenden Text, nicht über exakte CSS-Selektoren.
Das ist tendenziell stabiler gegen kleine Layout-Änderungen, kann aber bei
größeren Strukturänderungen der Seite ins Leere laufen. Beim ersten Lauf
unbedingt die Actions-Logs prüfen (Anzahl gefundener Objekte plausibel?).
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ohne-makler.net"

# Zwei Such-URLs: Stadt und Land getrennt, damit wir die Kategorie schon
# beim Scrapen kennen (unabhängig von filters.bestimme_kategorie, das dient
# hier nur als zusätzliche Absicherung)
SEARCH_URLS = {
    "stadt": f"{BASE_URL}/immobilien/wohnung-kaufen/bayern/kreis-augsburg-stadt/",
    "land": f"{BASE_URL}/immobilien/wohnung-kaufen/bayern/kreis-augsburg-land/",
}

HEADERS = {
    # Realistischer Browser-User-Agent. Kein Verschleiern der Bot-Natur
    # über gefälschte Header hinaus - wir geben uns nicht als spezifischen
    # echten Nutzer aus, nur als normaler Browser-Request.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

REQUEST_TIMEOUT = 20
DELAY_BETWEEN_REQUESTS = 2  # Sekunden, Rücksicht auf den Server

# Matched z.B. "/immobilie/484840/"
LISTING_LINK_RE = re.compile(r"/immobilie/(\d+)/")

# Preis, z.B. "279.000 €"
PRICE_RE = re.compile(r"([\d.]+)\s*€")

# PLZ + Ort + (Bezirk), z.B. "86153 Augsburg (Innenstadt)"
PLZ_ORT_RE = re.compile(r"(\d{5})\s+([A-Za-zÄÖÜäöüß\-\. ]+?)(?:\s*\(([^)]+)\))?(?:\s|$)")

# Zimmeranzahl, z.B. "2,5" oder "3" gefolgt von Fläche "70,15m²"
ZIMMER_FLAECHE_RE = re.compile(r"(\d+(?:,\d+)?)\s+(\d+(?:,\d+)?)\s*m²")


@dataclass
class Objekt:
    quelle: str = "ohne-makler.net"
    quelle_id: str = ""
    titel: str = ""
    url: str = ""
    kaufpreis: float | None = None
    plz: str | None = None
    ort: str | None = None
    ortsteil: str | None = None
    zimmer: float | None = None
    flaeche_qm: float | None = None
    baujahr: int | None = None
    energieeffizienzklasse: str | None = None
    objekttyp: str = "Wohnung"
    kategorie_hint: str = ""  # 'stadt' oder 'land', aus der Such-URL bekannt

    def to_dict(self) -> dict:
        return {
            "quelle": self.quelle,
            "quelle_id": self.quelle_id,
            "titel": self.titel,
            "url": self.url,
            "kaufpreis": self.kaufpreis,
            "plz": self.plz,
            "ort": self.ort,
            "ortsteil": self.ortsteil,
            "zimmer": self.zimmer,
            "flaeche_qm": self.flaeche_qm,
            "baujahr": self.baujahr,
            "energieeffizienzklasse": self.energieeffizienzklasse,
            "objekttyp": self.objekttyp,
        }


def _parse_listing_block(block_text: str, listing_id: str, url: str, kategorie_hint: str) -> Objekt | None:
    """
    Parsed einen einzelnen Objekt-Textblock (Title + Metadaten, wie sie in
    den Linktexten der Übersichtsseite stehen).
    """
    obj = Objekt(quelle_id=listing_id, url=url, kategorie_hint=kategorie_hint)

    # Rest des öffnenden Tags (z.B. '">' vom Ende von href="...") sowie
    # führende/nachfolgende Sonderzeichen abschneiden
    block_text = re.sub(r'^[">\s]+', "", block_text)

    price_match = PRICE_RE.search(block_text)
    if price_match:
        try:
            obj.kaufpreis = float(price_match.group(1).replace(".", ""))
        except ValueError:
            logger.warning("Preis konnte nicht geparst werden: %r", price_match.group(0))
        obj.titel = block_text[: price_match.start()].strip(" -")
    else:
        obj.titel = block_text.strip()

    plz_match = PLZ_ORT_RE.search(block_text)
    if plz_match:
        obj.plz = plz_match.group(1)
        obj.ort = plz_match.group(2).strip()
        obj.ortsteil = (plz_match.group(3) or "").strip() or None

    zf_match = ZIMMER_FLAECHE_RE.search(block_text)
    if zf_match:
        try:
            obj.zimmer = float(zf_match.group(1).replace(",", "."))
            obj.flaeche_qm = float(zf_match.group(2).replace(",", "."))
        except ValueError:
            logger.warning("Zimmer/Fläche konnte nicht geparst werden: %r", zf_match.group(0))

    # Baujahr und Energieeffizienzklasse stehen auf der Übersichtsseite i.d.R.
    # NICHT drin, sondern erst im Detail-Exposé. Bewusster Kompromiss für
    # Version 1: wir laden nicht jedes Detail-Exposé einzeln nach (deutlich
    # mehr Requests, langsamer, höheres Scraping-Risiko), sondern lassen
    # diese Felder hier leer. filters.py behandelt fehlende Werte als
    # "durchlassen, nicht ausschließen" - passt zu deiner Vorgabe bei der
    # Energieeffizienz.
    return obj


def _fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def scrape() -> list[dict]:
    """
    Scraped beide Kategorien (Stadt/Land) und gibt eine Liste von
    Objekt-dicts zurück. Wirft keine Exception bei Einzelfehlern einer
    Kategorie, sondern loggt und macht mit der anderen weiter - ein Fehler
    bei "land" soll nicht den kompletten Scan abbrechen.
    """
    alle_objekte: list[dict] = []

    for kategorie, url in SEARCH_URLS.items():
        try:
            raw_html = _fetch(url)
        except requests.RequestException as e:
            logger.error("ohne-makler.net (%s): Request fehlgeschlagen: %s", kategorie, e)
            continue

        matches = list(LISTING_LINK_RE.finditer(raw_html))
        gefunden_ids = set()
        for i, match in enumerate(matches):
            listing_id = match.group(1)
            if listing_id in gefunden_ids:
                continue
            gefunden_ids.add(listing_id)

            # Der beschreibende Text (Titel, Preis, PLZ, Zimmer, Fläche) steht
            # als Linktext NACH dem href-Attribut, also ab match.end(). Als
            # obere Grenze nehmen wir den Start des nächsten Treffers (oder
            # ein Cap von 500 Zeichen), damit sich Nachbar-Anzeigen nicht
            # gegenseitig kontaminieren.
            start = match.end()
            next_start = matches[i + 1].start() if i + 1 < len(matches) else len(raw_html)
            end = min(start + 500, next_start)
            context = raw_html[start:end]
            context_text = re.sub(r"<[^>]+>", " ", context)
            context_text = html.unescape(context_text)
            context_text = re.sub(r"\s+", " ", context_text).strip()

            listing_url = f"{BASE_URL}/immobilie/{listing_id}/"
            obj = _parse_listing_block(context_text, listing_id, listing_url, kategorie)
            if obj and obj.titel:
                alle_objekte.append(obj.to_dict())

        logger.info("ohne-makler.net (%s): %d Objekte gefunden", kategorie, len(gefunden_ids))
        time.sleep(DELAY_BETWEEN_REQUESTS)

    return alle_objekte


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ergebnisse = scrape()
    print(f"\n{len(ergebnisse)} Objekte gefunden:\n")
    for o in ergebnisse:
        print(o)
