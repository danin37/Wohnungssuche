# Immo-Scan

Täglicher automatischer Scan von zvg-portal.de und ohne-makler.net nach
Eigentumswohnungen in Augsburg Stadt/Land, mit E-Mail-Zusammenfassung.

## Status: Version 1 - vor dem ersten Live-Test

Die Kernlogik (Filter, Tracking, E-Mail-Aufbau) ist unit-getestet.
**Die beiden Scraper-Module wurden noch NICHT gegen die echten Live-Seiten
getestet** (nur gegen synthetische Beispieldaten, die den bekannten
Textmustern nachempfunden sind). Vor dem ersten produktiven Lauf unbedingt
Schritt 6 (lokaler Test) und Schritt 7 (Fehlerdiagnose im Actions-Log)
durchgehen.

## Quellen und ihre Zuverlässigkeit

| Quelle | Konfidenz Scraper-Logik | Bekannte Einschränkungen |
|---|---|---|
| ohne-makler.net | mittel-hoch | Baujahr & EEK stehen nicht auf der Übersichtsseite, bleiben leer (werden dadurch NICHT ausgeschlossen, siehe filters.py) |
| zvg-portal.de | mittel | Ort-Feld wird nicht serverseitig gefiltert (nur Land=Bayern), Geo-Filterung läuft komplett über filters.py anhand des Rohtexts. Kein Kaufpreis, nur Verkehrswert. |

## Lokaler Test vor Deployment

```bash
pip install -r requirements.txt --break-system-packages

# Einzelne Quelle isoliert testen (druckt gefundene Objekte, kein Mail-Versand):
python3 -m scraper.ohne_makler
python3 -m scraper.zvg_portal

# Kompletten Lauf testen (braucht die drei Umgebungsvariablen für den Mail-Versand):
export GMAIL_ADDRESS="deine@gmail.com"
export GMAIL_APP_PASSWORD="dein-app-passwort"
export RECIPIENT_EMAIL="empfaenger@example.com"
python3 main.py
```

## Fehlerdiagnose im GitHub Actions Log

- **"Kein <form>-Element gefunden" (zvg-portal.de)**: Seite hat sich strukturell geändert, oder Request wurde blockiert (Statuscode prüfen).
- **"0 Objekte gefunden" bei einer Quelle, obwohl es online sichtbar Angebote gibt**: Die Regex-/Selektor-Muster greifen nicht mehr - Seite hat vermutlich das Layout geändert. HTML der Seite erneut prüfen und Muster in `scraper/ohne_makler.py` bzw. `scraper/zvg_portal.py` anpassen.
- **"E-Mail-Versand fehlgeschlagen"**: Meist falsches App-Passwort oder 2FA nicht aktiv - GMAIL_APP_PASSWORD-Secret neu generieren.
- **Workflow läuft, aber kein Commit von results.json**: Normal, wenn sich der Datenstand nicht geändert hat (bewusstes Verhalten, siehe daily-scan.yml).

## Manuellen Testlauf auslösen

GitHub → Actions-Tab → "Täglicher Immo-Scan" → "Run workflow" (nutzt `workflow_dispatch`, muss nicht auf 07:00 Uhr gewartet werden).
