"""
Orchestriert den täglichen Immo-Scan:
1. Alle Quellen scrapen
2. Hartfilter anwenden (filters.py)
3. Mit letztem Datenstand vergleichen (tracking.py)
4. E-Mail versenden (mailer.py)
5. Neuen Datenstand speichern (data/results.json)

Design-Prinzip: Ein Fehler in einer einzelnen Quelle oder im Mail-Versand
soll den gesamten Lauf nicht zum Absturz bringen. Am Ende steht immer ein
möglichst aussagekräftiges Log, auch wenn Teile fehlgeschlagen sind.
"""

from __future__ import annotations

import logging
import sys

from scraper import filters, mailer, ohne_makler, tracking, zvg_portal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def scrape_alle_quellen() -> list[dict]:
    alle_rohobjekte: list[dict] = []

    quellen = [
        ("ohne-makler.net", ohne_makler.scrape),
        ("zvg-portal.de", zvg_portal.scrape),
    ]

    for name, scrape_fn in quellen:
        logger.info("Starte Scan: %s", name)
        try:
            objekte = scrape_fn()
            logger.info("%s: %d Rohdatensätze erhalten", name, len(objekte))
            alle_rohobjekte.extend(objekte)
        except Exception:
            # Absichtlich breites except: eine kaputte Quelle soll den Lauf
            # der anderen Quelle nicht verhindern. Der volle Traceback landet
            # im Actions-Log (exc_info=True) für die Fehlerdiagnose.
            logger.exception("%s: Unerwarteter Fehler beim Scraping, Quelle wird übersprungen.", name)

    return alle_rohobjekte


def filtere_objekte(rohobjekte: list[dict]) -> tuple[list[dict], list[dict]]:
    stadt, land = [], []
    for objekt in rohobjekte:
        bestanden, kategorie, warnungen = filters.passt_hartfilter(objekt)
        if not bestanden:
            continue
        objekt = dict(objekt)
        objekt["warnungen"] = warnungen
        if kategorie == "stadt":
            stadt.append(objekt)
        elif kategorie == "land":
            land.append(objekt)
    return stadt, land


def main() -> int:
    logger.info("=== Immo-Scan gestartet ===")

    rohobjekte = scrape_alle_quellen()
    if not rohobjekte:
        logger.warning(
            "Keine Rohdaten von irgendeiner Quelle erhalten. Das ist verdächtig - "
            "entweder sind wirklich beide Quellen leer, oder beide Scraper sind "
            "kaputt. Bitte Actions-Log oben auf Fehler prüfen."
        )

    stadt_objekte, land_objekte = filtere_objekte(rohobjekte)
    logger.info("Nach Hartfiltern: %d Stadt, %d Land", len(stadt_objekte), len(land_objekte))

    letzter_stand = tracking.lade_letzten_stand()
    stadt_objekte, stand_stadt = tracking.vergleiche(stadt_objekte, letzter_stand)
    land_objekte, stand_land = tracking.vergleiche(land_objekte, letzter_stand)
    neuer_stand = {**stand_stadt, **stand_land}

    betreff, html_body = mailer.baue_email_html(stadt_objekte, land_objekte)
    versand_erfolgreich = mailer.sende_email(betreff, html_body)

    # results.json wird auch gespeichert, wenn der Mail-Versand fehlschlägt -
    # sonst würde bei einem SMTP-Problem am nächsten Tag jedes Objekt fälsch-
    # licherweise wieder als "neu" markiert, weil der Datenstand nie
    # aktualisiert wurde.
    tracking.speichere_stand(neuer_stand)
    logger.info("Datenstand gespeichert: %d Objekte insgesamt.", len(neuer_stand))

    logger.info("=== Immo-Scan abgeschlossen ===")

    if not versand_erfolgreich:
        logger.error("Lauf beendet, aber E-Mail-Versand ist fehlgeschlagen - bitte GMAIL_* Secrets prüfen.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
