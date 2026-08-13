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
from bs4 import BeautifulSoup

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
    bild_url: str | None = None
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
            "bild_url": self.bild_url,
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

    plz_match = PLZ_ORT_RE.search(block_text)
    if plz_match:
        obj.plz = plz_match.group(1)
        obj.ort = plz_match.group(2).strip()
        obj.ortsteil = (plz_match.group(3) or "").strip() or None

    # Titel = der Text ZWISCHEN Preis-Ende und PLZ-Beginn. Robust gegen
    # beide beobachteten Reihenfolgen ("Preis ... Titel ... PLZ" und
    # "Titel ... Preis ... PLZ"), weil wir nicht mehr davon ausgehen, dass
    # der Preis am Anfang oder Ende steht - nur dass PLZ/Ort dem Titel folgt.
    titel_start = price_match.end() if price_match else 0
    titel_end = plz_match.start() if plz_match else len(block_text)
    if titel_end > titel_start:
        obj.titel = block_text[titel_start:titel_end].strip(" -")
    else:
        # Fallback falls PLZ vor dem Preis auftaucht (unerwartete Reihenfolge)
        # oder gar nichts erkannt wurde: ganzen Text als Titel nehmen, besser
        # als das Objekt komplett zu verlieren.
        obj.titel = block_text.strip(" -")

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

        soup = BeautifulSoup(raw_html, "html.parser")
        gefunden_ids = set()
        diag_zaehler = 0

        for link in soup.find_all("a", href=LISTING_LINK_RE):
            match = LISTING_LINK_RE.search(link.get("href", ""))
            if not match:
                continue
            listing_id = match.group(1)
            if listing_id in gefunden_ids:
                continue
            gefunden_ids.add(listing_id)

            # Der eigentliche Objekttext (Titel, Preis, PLZ, Ort, Zimmer,
            # Fläche) steht im alt-Attribut des Bildes innerhalb des Links,
            # nicht als sichtbarer Fließtext daneben (per Live-Test bestätigt).
            # Fallback auf den sichtbaren Linktext, falls kein Bild/alt da ist.
            img = link.find("img")
            alt_text = (img.get("alt", "") if img else "") or ""
            sichtbarer_text = link.get_text(separator=" ", strip=True)
            text_quelle = alt_text if len(alt_text) > len(sichtbarer_text) else sichtbarer_text
            text_quelle = html.unescape(text_quelle)
            text_quelle = re.sub(r"\s+", " ", text_quelle).strip()

            bild_url = None
            if img:
                # Bei Lazy-Loading (erkennbar am loading="lazy"-Attribut, das
                # wir in den Live-Daten gesehen haben) steht im "src" oft nur
                # ein Platzhalter-Pixel, die echte URL liegt in "data-src"
                # oder im ersten Eintrag von "srcset". Deshalb hier zuerst
                # prüfen, "src" erst als letzter Fallback.
                srcset = img.get("srcset", "")
                srcset_erste_url = srcset.split(",")[0].strip().split(" ")[0] if srcset else None
                src = img.get("data-src") or srcset_erste_url or img.get("src")
                if src:
                    bild_url = src if src.startswith("http") else f"{BASE_URL}/{src.lstrip('/')}"

            if diag_zaehler < 2 and img:
                logger.info(
                    "DIAGNOSE Bild-Attribute Objekt #%d: src=%r data-src=%r srcset=%r -> gewählt=%r",
                    diag_zaehler + 1, img.get("src"), img.get("data-src"), (img.get("srcset") or "")[:150], bild_url,
                )
                diag_zaehler += 1

            listing_url = f"{BASE_URL}/immobilie/{listing_id}/"
            obj = _parse_listing_block(text_quelle, listing_id, listing_url, kategorie)
            if obj:
                obj.bild_url = bild_url

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
