"""
Scraper für zvg-portal.de (bundesweites Zwangsversteigerungsportal der
Landesjustizverwaltungen).

WICHTIGER HINWEIS ZUR ZUVERLÄSSIGKEIT:
Anders als bei ohne-makler.net konnte ich hier die exakten Formularfeld-
Namen nicht direkt einsehen (nur die gerenderte Textstruktur). Deshalb liest
dieser Scraper das Suchformular zur Laufzeit selbst aus (Feldnamen, aktuelle
Options-Werte) statt sie hart zu kodieren - das ist robuster gegen falsch
geratene Namen, aber abhängig davon, dass die grobe Formularstruktur
(Land-Auswahl mit "Bayern"-Option, Objektart-Auswahl mit "Eigentumswohnung"-
Optionen) so bleibt wie beim Bau dieses Skripts beobachtet.

Bewusste Vereinfachung: Es wird NUR nach Land=Bayern und Objektart=ETW
serverseitig gefiltert, nicht nach Ort/PLZ (das Ort-Feld-Verhalten war nicht
zweifelsfrei zu bestimmen). Die Eingrenzung auf Augsburg Stadt/Land
übernimmt filters.py im Anschluss anhand der in den Ergebnissen enthaltenen
Lage-Angaben. Dadurch werden ggf. mehr Datensätze geladen als nötig
(bayernweit statt nur Augsburg-Region), aber die Ergebnisse sind korrekt.
"""

from __future__ import annotations

import html
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.zvg-portal.de"
SEARCH_FORM_URL = f"{BASE_URL}/index.php?button=Termine+suchen"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

REQUEST_TIMEOUT = 20

# Wonach wir in den Options-Texten suchen, um die richtigen Werte zu finden
LAND_OPTION_TEXT = "Bayern"
OBJEKT_OPTION_TEXTS = [
    "Eigentumswohnung (1 bis 2 Zimmer)",
    "Eigentumswohnung (3 bis 4 Zimmer)",
]

VERKEHRSWERT_RE = re.compile(r"([\d.]+,\d{2})\s*€")
TERMIN_DATUM_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
PLZ_RE = re.compile(r"\b(\d{5})\b")


def _get_form_fields(soup: BeautifulSoup) -> tuple[str, str, dict]:
    """
    Findet das Suchformular und extrahiert action-URL, HTTP-Methode und
    alle aktuellen Feldwerte (inkl. versteckter Felder wie Session-Tokens).
    """
    form = soup.find("form")
    if form is None:
        raise RuntimeError("Kein <form>-Element auf der Suchseite gefunden - Seitenstruktur hat sich vermutlich geändert.")

    action = form.get("action") or SEARCH_FORM_URL
    if not action.startswith("http"):
        action = f"{BASE_URL}/{action.lstrip('/')}"
    method = (form.get("method") or "GET").upper()

    fields: dict = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ in ("submit", "reset", "button", "image"):
            continue
        if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        fields[name] = inp.get("value", "")

    return action, method, fields


def _find_select_value(soup: BeautifulSoup, option_text: str) -> tuple[str, str] | None:
    """
    Sucht über alle <select>-Elemente nach einer <option>, deren sichtbarer
    Text option_text enthält. Gibt (select_name, option_value) zurück.
    """
    for select in soup.find_all("select"):
        name = select.get("name")
        if not name:
            continue
        for option in select.find_all("option"):
            if option_text.lower() in option.get_text(strip=True).lower():
                return name, option.get("value", option.get_text(strip=True))
    return None


def _build_search_params(soup: BeautifulSoup) -> tuple[str, str, dict] | None:
    action, method, fields = _get_form_fields(soup)

    land_match = _find_select_value(soup, LAND_OPTION_TEXT)
    if land_match is None:
        logger.error("zvg-portal.de: Konnte Land-Auswahlfeld für '%s' nicht finden.", LAND_OPTION_TEXT)
        return None
    land_name, land_value = land_match
    fields[land_name] = land_value

    objekt_values = []
    objekt_name = None
    for text in OBJEKT_OPTION_TEXTS:
        match = _find_select_value(soup, text)
        if match is None:
            logger.warning("zvg-portal.de: Objektart-Option '%s' nicht gefunden, wird übersprungen.", text)
            continue
        objekt_name, value = match
        objekt_values.append(value)

    if objekt_name and objekt_values:
        # Mehrfachauswahl: als Liste setzen (requests kodiert Listen korrekt
        # als wiederholte Query-Parameter, was dem üblichen HTML-Multi-Select
        # -Verhalten entspricht)
        fields[objekt_name] = objekt_values

    return action, method, fields


def _extract_listings(results_html: str) -> list[dict]:
    soup = BeautifulSoup(results_html, "html.parser")
    objekte = []

    # Generischer Ansatz: jede Tabellenzeile, die einen Link auf einen
    # Termin/Aktenzeichen enthält, wird als ein Datensatz behandelt.
    rows = soup.find_all("tr")
    if not rows:
        logger.warning("zvg-portal.de: Keine Tabellenzeilen in den Suchergebnissen gefunden.")
        logger.info("DIAGNOSE zvg-portal.de: Antwort-Ausschnitt (erste 500 Zeichen des sichtbaren Texts): %r", soup.get_text(separator=" ", strip=True)[:500])
        return []

    for row in rows:
        link = row.find("a", href=True)
        row_text = html.unescape(row.get_text(separator=" | ", strip=True))
        if not row_text or len(row_text) < 10:
            continue

        verkehrswert = None
        vw_match = VERKEHRSWERT_RE.search(row_text)
        if vw_match:
            try:
                verkehrswert = float(vw_match.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                pass

        termin_match = TERMIN_DATUM_RE.search(row_text)
        termin = termin_match.group(1) if termin_match else None

        plz_match = PLZ_RE.search(row_text)
        plz = plz_match.group(1) if plz_match else None

        detail_url = None
        if link:
            href = link["href"]
            detail_url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"

        # Nur Zeilen aufnehmen, die plausibel ein Objekt-Datensatz sind
        # (Verkehrswert oder Termin oder Detail-Link vorhanden)
        if not (verkehrswert or termin or detail_url):
            continue

        objekte.append({
            "quelle": "zvg-portal.de",
            "quelle_id": (detail_url or row_text[:50]),
            "titel": row_text[:200],
            "url": detail_url or SEARCH_FORM_URL,
            "kaufpreis": None,  # ZV hat keinen Kaufpreis, sondern Verkehrswert
            "verkehrswert": verkehrswert,
            "termin": termin,
            "plz": plz,
            # Der Rohtext der Zeile enthält Ort/Lage-Angaben, auch wenn wir
            # sie nicht sauber isolieren können. filters.bestimme_kategorie()
            # sucht "augsburg"/Gemeindenamen als Teilstring - das funktioniert
            # auch gegen den Rohtext. Mit ort=None würde JEDES Objekt hier
            # fälschlich rausgefiltert werden, da die Kategorie-Erkennung
            # sonst nichts zum Prüfen hätte.
            "ort": row_text,
            "ortsteil": None,
            "zimmer": None,
            "flaeche_qm": None,
            "baujahr": None,
            "energieeffizienzklasse": None,
            "objekttyp": "Eigentumswohnung",
        })

    return objekte


def scrape() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(SEARCH_FORM_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("zvg-portal.de: Suchformular konnte nicht geladen werden: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    built = _build_search_params(soup)
    if built is None:
        logger.error("zvg-portal.de: Suchparameter konnten nicht ermittelt werden, überspringe Quelle.")
        return []

    action, method, fields = built
    logger.info("zvg-portal.de: Suche wird ausgeführt (%s %s)", method, action)

    try:
        if method == "POST":
            result_resp = session.post(action, data=fields, timeout=REQUEST_TIMEOUT)
        else:
            result_resp = session.get(action, params=fields, timeout=REQUEST_TIMEOUT)
        result_resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("zvg-portal.de: Suchanfrage fehlgeschlagen: %s", e)
        return []

    logger.info("zvg-portal.de: Ergebnis-URL nach Suche: %s", result_resp.url)
    objekte = _extract_listings(result_resp.text)
    logger.info("zvg-portal.de: %d Datensätze extrahiert (vor Geo-Filterung)", len(objekte))
    return objekte


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ergebnisse = scrape()
    print(f"\n{len(ergebnisse)} Objekte gefunden:\n")
    for o in ergebnisse[:10]:
        print(o)
