"""
Zentrale Hartfilter für alle Objekte, unabhängig von der Quelle.

Jede Quelle liefert ein einheitliches dict pro Objekt (siehe README für das
Schema). Dieses Modul entscheidet, ob ein Objekt die Grundkriterien erfüllt.
"""

# ---- Geografie -------------------------------------------------------

# Ausgeschlossene Augsburg-Stadtbezirke (per PLZ, härtester und zuverlässigster
# Anker, da Ortsteil-Namen in Anzeigen uneinheitlich geschrieben werden)
EXCLUDED_PLZ_PREFIXES = {
    "86154",  # Oberhausen
    "86156",  # Oberhausen (Teil)
    "86167",  # Lechhausen
    "86179",  # Hammerschmiede (Teil) UND Haunstetten teilen sich diese PLZ.
               # Bewusste Entscheidung (Daniel, 08/2026): 86179 komplett
               # ausschließen, lieber zu streng als Hammerschmiede reinlassen.
               # Konsequenz: echte Haunstetten-Objekte werden dadurch ebenfalls
               # gefiltert und tauchen NICHT in der Mail auf.
}

# Erlaubte Landkreis-Gemeinden (Kategorie "Augsburg Land")
ALLOWED_LAND_GEMEINDEN = {
    "neusäß", "neusaess", "neusass",
    "diedorf",
    "aystetten",
    "zusmarshausen",
    "horgau",
    "adelsried",
    "dinkelscherben",
}

MAX_KAUFPREIS = 300_000
MIN_BAUJAHR = 1950

# EEK-Ausschluss: nur D oder besser (A+, A, B, C, D). E/F/G/H raus.
# Fehlende Angabe wird NICHT ausgeschlossen (siehe check_energieeffizienz).
EEK_RANKING = ["A+", "A", "B", "C", "D", "E", "F", "G", "H"]
MIN_EEK = "D"


def _eek_index(eek: str) -> int | None:
    if not eek:
        return None
    eek = eek.strip().upper()
    return EEK_RANKING.index(eek) if eek in EEK_RANKING else None


def check_energieeffizienz(objekt: dict) -> bool:
    """
    True, wenn EEK D oder besser ist, ODER keine Angabe vorhanden ist.
    Nur explizit E/F/G/H führt zum Ausschluss.
    """
    eek = objekt.get("energieeffizienzklasse")
    idx = _eek_index(eek)
    if idx is None:
        # Keine oder nicht erkannte Angabe -> nicht ausschließen (User-Vorgabe)
        return True
    return idx <= EEK_RANKING.index(MIN_EEK)


def check_plz_ausschluss(objekt: dict) -> bool:
    """True, wenn die PLZ NICHT in einem ausgeschlossenen Stadtbezirk liegt."""
    plz = (objekt.get("plz") or "").strip()
    if not plz:
        # Keine PLZ bekannt -> kann nicht sicher ausschließen, durchlassen
        # und im Score/Flag als "PLZ ungeprüft" kennzeichnen (siehe main.py)
        return True
    return plz not in EXCLUDED_PLZ_PREFIXES


def bestimme_kategorie(objekt: dict) -> str | None:
    """
    Gibt 'stadt', 'land' oder None zurück (None = weder noch, komplett raus).
    """
    ort = (objekt.get("ort") or "").lower()
    plz = (objekt.get("plz") or "").strip()

    if "augsburg" in ort and plz not in EXCLUDED_PLZ_PREFIXES:
        return "stadt"

    for gemeinde in ALLOWED_LAND_GEMEINDEN:
        if gemeinde in ort:
            return "land"

    return None


def check_preis(objekt: dict) -> bool:
    preis = objekt.get("kaufpreis")
    if preis is None:
        # ZVG-Objekte haben keinen Kaufpreis, sondern einen Verkehrswert -
        # als Fallback verwenden, sonst würde der Preisfilter für diese
        # Quelle wirkungslos bleiben.
        preis = objekt.get("verkehrswert")
    if preis is None:
        # Wirklich kein Preis-Anhaltspunkt vorhanden -> durchlassen, manuell prüfen
        return True
    return preis <= MAX_KAUFPREIS


def check_baujahr(objekt: dict) -> bool:
    baujahr = objekt.get("baujahr")
    if baujahr is None:
        # Kein Baujahr bekannt -> durchlassen, manuell prüfen
        return True
    return baujahr >= MIN_BAUJAHR


def check_objekttyp(objekt: dict) -> bool:
    """Nur Eigentumswohnungen. Ausschluss von Haus/MFH/Grundstück/Gewerbe/Garage/Stellplatz."""
    typ = (objekt.get("objekttyp") or "").lower()
    ausschluss_begriffe = [
        "haus", "mfh", "mehrfamilien", "grundstück", "grundstueck",
        "gewerbe", "garage", "stellplatz", "büro", "buero", "halle",
    ]
    if any(b in typ for b in ausschluss_begriffe):
        return False
    # Positivkriterium: muss "wohnung" enthalten (oder Typ unbekannt -> durchlassen)
    if typ and "wohnung" not in typ:
        return False
    return True


def passt_hartfilter(objekt: dict) -> tuple[bool, str | None, list[str]]:
    """
    Prüft alle Hartfilter.
    Returns: (bestanden: bool, kategorie: 'stadt'|'land'|None, warnungen: list[str])
    warnungen sind Hinweise wie "PLZ unbekannt, ungeprüft" - Objekt wird trotzdem
    gezeigt, aber im Mail-Body markiert.
    """
    warnungen = []

    if not objekt.get("plz"):
        warnungen.append("PLZ unbekannt – Bezirksausschluss ungeprüft")
    if objekt.get("kaufpreis") is None:
        warnungen.append("Kaufpreis unbekannt")
    if objekt.get("baujahr") is None:
        warnungen.append("Baujahr unbekannt")
    if not objekt.get("energieeffizienzklasse"):
        warnungen.append("Energieeffizienzklasse unbekannt")

    if not check_objekttyp(objekt):
        return False, None, warnungen
    if not check_preis(objekt):
        return False, None, warnungen
    if not check_baujahr(objekt):
        return False, None, warnungen
    if not check_energieeffizienz(objekt):
        return False, None, warnungen

    kategorie = bestimme_kategorie(objekt)
    if kategorie is None:
        return False, None, warnungen

    return True, kategorie, warnungen
