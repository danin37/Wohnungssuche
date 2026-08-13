"""
Baut die HTML-E-Mail und versendet sie über Gmail SMTP.

Zugangsdaten kommen ausschließlich aus Umgebungsvariablen (die im GitHub
Actions Workflow aus den Secrets gesetzt werden) - stehen nie im Code.
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

STATUS_LABELS = {
    "neu": "🆕 NEU",
    "preisreduktion": "📉 PREISREDUKTION",
    "lang_online": "⏳ LANG ONLINE",
    "unveraendert": "➡️ UNVERÄNDERT",
}


def _format_preis(objekt: dict) -> str:
    preis = objekt.get("kaufpreis")
    label = "Kaufpreis"
    if preis is None:
        preis = objekt.get("verkehrswert")
        label = "Verkehrswert"
    if preis is None:
        return "Preis unbekannt"
    return f"{label}: {preis:,.0f} €".replace(",", ".")


def _format_objekt_html(objekt: dict) -> str:
    status_badges = " ".join(STATUS_LABELS.get(s, s) for s in objekt.get("status", []))

    preisaenderung_html = ""
    if objekt.get("preisaenderung"):
        pa = objekt["preisaenderung"]
        preisaenderung_html = f" (-{pa['differenz']:,.0f} € / -{pa['prozent']}%)".replace(",", ".")

    warnungen = objekt.get("warnungen") or []
    warnungen_html = ""
    if warnungen:
        warnungen_html = (
            "<div style='color:#a15c00;font-size:0.85em;margin-top:4px;'>⚠ "
            + ", ".join(warnungen)
            + "</div>"
        )

    zimmer = objekt.get("zimmer")
    flaeche = objekt.get("flaeche_qm")
    baujahr = objekt.get("baujahr")
    eek = objekt.get("energieeffizienzklasse")

    meta_teile = [_format_preis(objekt)]
    if flaeche:
        meta_teile.append(f"{flaeche} m²")
    if flaeche and objekt.get("kaufpreis"):
        meta_teile.append(f"{objekt['kaufpreis'] / flaeche:,.0f} €/m²".replace(",", "."))
    if zimmer:
        meta_teile.append(f"{zimmer} Zi.")
    if baujahr:
        meta_teile.append(f"Bj. {baujahr}")
    meta_teile.append(f"EEK {eek}" if eek else "EEK unbekannt")

    titel = objekt.get("titel", "(ohne Titel)")
    url = objekt.get("url", "#")
    quelle = objekt.get("quelle", "")

    return f"""
    <div style="border:1px solid #ddd; border-radius:6px; padding:12px; margin-bottom:10px;">
      <div style="font-size:0.8em; color:#888;">{quelle} {preisaenderung_html and ("| " + status_badges) or status_badges}{preisaenderung_html}</div>
      <div style="font-weight:600; margin:4px 0;"><a href="{url}" style="color:#1a4d8f; text-decoration:none;">{titel}</a></div>
      <div style="font-size:0.9em; color:#333;">{' | '.join(meta_teile)}</div>
      {warnungen_html}
    </div>
    """


def _bereich_html(titel: str, objekte: list[dict]) -> str:
    if not objekte:
        return f"<h3>{titel}</h3><p style='color:#888;'>Keine Treffer.</p>"
    objekte_sortiert = sorted(
        objekte,
        key=lambda o: ("preisreduktion" not in o.get("status", []), "neu" not in o.get("status", [])),
    )
    body = "".join(_format_objekt_html(o) for o in objekte_sortiert)
    return f"<h3>{titel} ({len(objekte)})</h3>{body}"


def baue_email_html(stadt_objekte: list[dict], land_objekte: list[dict]) -> tuple[str, str]:
    alle = stadt_objekte + land_objekte
    neue = [o for o in alle if "neu" in o.get("status", [])]
    preisreduktionen = [o for o in alle if "preisreduktion" in o.get("status", [])]

    heute = date.today().strftime("%d.%m.%Y")
    betreff = f"Immo-Scan {heute}: {len(neue)} neue Treffer (Stadt: {len(stadt_objekte)} | Land: {len(land_objekte)})"

    top_pick = neue[0] if neue else (alle[0] if alle else None)
    top_pick_html = (
        f"<a href='{top_pick['url']}'>{top_pick.get('titel', '(ohne Titel)')}</a>"
        if top_pick else "Keine Objekte gefunden."
    )

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width:700px; margin:0 auto;">
      <h2>Immo-Scan {heute}</h2>

      {_bereich_html("Augsburg Stadt", stadt_objekte)}
      {_bereich_html("Augsburg Land", land_objekte)}

      <h3>Preisreduktionen heute ({len(preisreduktionen)})</h3>
      {"".join(_format_objekt_html(o) for o in preisreduktionen) if preisreduktionen else "<p style='color:#888;'>Keine.</p>"}

      <hr style="margin:24px 0; border:none; border-top:1px solid #ddd;">
      <h3>Zusammenfassung</h3>
      <p>
        Gesamt gescannt: {len(alle)} Objekte<br>
        Neue Treffer: {len(neue)}<br>
        Preisreduktionen: {len(preisreduktionen)}<br>
        Top-Pick heute: {top_pick_html}
      </p>
    </body>
    </html>
    """
    return betreff, html_body


def sende_email(betreff: str, html_body: str) -> bool:
    """
    Gibt True zurück bei Erfolg, False bei Fehler (wirft absichtlich keine
    Exception, damit main.py entscheiden kann, ob der Actions-Run trotzdem
    als "erfolgreich" gilt, wenn nur der Versand scheitert aber
    results.json trotzdem committet werden soll).
    """
    absender = os.environ.get("GMAIL_ADDRESS")
    passwort = os.environ.get("GMAIL_APP_PASSWORD")
    empfaenger = os.environ.get("RECIPIENT_EMAIL")

    fehlende = [n for n, v in [("GMAIL_ADDRESS", absender), ("GMAIL_APP_PASSWORD", passwort), ("RECIPIENT_EMAIL", empfaenger)] if not v]
    if fehlende:
        logger.error("E-Mail-Versand übersprungen - fehlende Umgebungsvariablen: %s", ", ".join(fehlende))
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = betreff
    msg["From"] = absender
    msg["To"] = empfaenger
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(absender, passwort)
            server.sendmail(absender, [empfaenger], msg.as_string())
        logger.info("E-Mail erfolgreich versendet an %s", empfaenger)
        return True
    except smtplib.SMTPException as e:
        logger.error("E-Mail-Versand fehlgeschlagen: %s", e)
        return False
