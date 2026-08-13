"""
Vergleicht den aktuellen Scan mit dem letzten gespeicherten Datenstand
(data/results.json), um neue Objekte, Preisreduktionen und "lang online"
markierte Objekte zu erkennen.

Objekt-Identität: quelle + quelle_id (eindeutig pro Quelle). Das ist
robuster als z.B. die URL, falls sich URL-Parameter mal ändern.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

RESULTS_PATH = Path(__file__).resolve().parent.parent / "data" / "results.json"
LANG_ONLINE_TAGE = 60


def _objekt_key(objekt: dict) -> str:
    return f"{objekt.get('quelle')}::{objekt.get('quelle_id')}"


def lade_letzten_stand() -> dict:
    """
    Lädt den letzten Datenstand. Gibt ein leeres dict zurück, wenn die Datei
    nicht existiert (erster Lauf) oder beschädigt ist (korruptes JSON soll
    den Scan nicht komplett zum Absturz bringen, sondern wie ein Neustart
    behandelt werden - mit einer deutlichen Warnung im Log).
    """
    if not RESULTS_PATH.exists():
        logger.info("Kein vorheriger Datenstand gefunden (%s) - das ist vermutlich der erste Lauf.", RESULTS_PATH)
        return {}

    try:
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(
            "results.json konnte nicht gelesen werden (%s) - behandle als leeren Datenstand. "
            "Das bedeutet: ALLE heutigen Treffer werden als 'NEU' markiert, auch wenn sie es "
            "nicht sind. Bitte results.json manuell prüfen.",
            e,
        )
        return {}


def speichere_stand(stand: dict) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(stand, f, ensure_ascii=False, indent=2, sort_keys=True)


def vergleiche(aktuelle_objekte: list[dict], letzter_stand: dict) -> tuple[list[dict], dict]:
    """
    Reichert jedes aktuelle Objekt mit einem 'status' und ggf.
    'preisaenderung' an, und gibt den neuen zu speichernden Stand zurück.

    status: 'neu' | 'preisreduktion' | 'lang_online' | 'unveraendert'
    (lang_online und preisreduktion schließen sich nicht aus - preisreduktion
    hat Vorrang in der Anzeige, lang_online wird zusätzlich als Flag geführt)
    """
    heute = date.today().isoformat()
    neuer_stand: dict = {}
    angereicherte_objekte: list[dict] = []

    for objekt in aktuelle_objekte:
        key = _objekt_key(objekt)
        alter_eintrag = letzter_stand.get(key)

        objekt = dict(objekt)  # Kopie, um das Original nicht zu mutieren
        objekt["status"] = []
        objekt["preisaenderung"] = None

        if alter_eintrag is None:
            objekt["status"].append("neu")
            objekt["erstmals_gesehen"] = heute
        else:
            objekt["erstmals_gesehen"] = alter_eintrag.get("erstmals_gesehen", heute)

            alter_preis = alter_eintrag.get("kaufpreis") or alter_eintrag.get("verkehrswert")
            neuer_preis = objekt.get("kaufpreis") or objekt.get("verkehrswert")
            if alter_preis is not None and neuer_preis is not None and neuer_preis < alter_preis:
                diff = alter_preis - neuer_preis
                prozent = (diff / alter_preis) * 100 if alter_preis else 0
                objekt["preisaenderung"] = {"differenz": diff, "prozent": round(prozent, 1)}
                objekt["status"].append("preisreduktion")

            try:
                erstmals = date.fromisoformat(objekt["erstmals_gesehen"])
                if (date.today() - erstmals) > timedelta(days=LANG_ONLINE_TAGE):
                    objekt["status"].append("lang_online")
            except ValueError:
                pass

            if not objekt["status"]:
                objekt["status"].append("unveraendert")

        objekt["zuletzt_gesehen"] = heute
        neuer_stand[key] = objekt
        angereicherte_objekte.append(objekt)

    entfernte_anzahl = len(set(letzter_stand.keys()) - set(neuer_stand.keys()))
    if entfernte_anzahl:
        logger.info("%d Objekte sind seit dem letzten Scan vom Markt genommen worden (nicht mehr in den Ergebnissen).", entfernte_anzahl)

    return angereicherte_objekte, neuer_stand
