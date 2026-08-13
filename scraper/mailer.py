"""
Baut die HTML-E-Mail im Look eines professionellen Maklerbriefs (Marke:
"DN Real Estate", fiktiv) und versendet sie über Gmail SMTP.

Bewusst tabellenbasiertes Layout statt Flexbox/Grid: E-Mail-Clients (v.a.
Outlook) unterstützen modernes CSS sehr unzuverlässig, Tabellen mit Inline-
Styles sind der robusteste gemeinsame Nenner für E-Mail-HTML.

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

# Markenfarben "DN Real Estate"
FARBE_NAVY = "#1B2A41"
FARBE_GOLD = "#B7975A"
FARBE_GOLD_HELL = "#E8DCC4"
FARBE_HINTERGRUND = "#F5F3EF"
FARBE_TEXT = "#2A2A28"
FARBE_TEXT_MUTED = "#6B6B66"
FARBE_WARNUNG = "#A15C00"
FARBE_ERFOLG = "#2E7D4F"

STATUS_LABELS = {
    "neu": ("NEU", FARBE_ERFOLG),
    "preisreduktion": ("PREISREDUKTION", "#B7440E"),
    "lang_online": ("LANG ONLINE", FARBE_WARNUNG),
    "unveraendert": ("UNVERÄNDERT", FARBE_TEXT_MUTED),
}

PLATZHALTER_BILD_SVG_DATA_URI = (
    "data:image/svg+xml;charset=UTF-8,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='120' "
    "viewBox='0 0 160 120'%3E%3Crect width='160' height='120' fill='%23E8DCC4'/%3E"
    "%3Ctext x='80' y='64' font-family='Georgia,serif' font-size='11' "
    "fill='%23B7975A' text-anchor='middle'%3Ekein Foto%3C/text%3E%3C/svg%3E"
)


def _format_preis(objekt: dict) -> str:
    preis = objekt.get("kaufpreis")
    label = "Kaufpreis"
    if preis is None:
        preis = objekt.get("verkehrswert")
        label = "Verkehrswert"
    if preis is None:
        return "Preis auf Anfrage"
    formatiert = f"{preis:,.0f} €".replace(",", ".")
    return f"{formatiert}" if label == "Kaufpreis" else f"{formatiert} (Verkehrswert)"


def _status_badges_html(objekt: dict) -> str:
    badges = []
    for status in objekt.get("status", []):
        if status == "unveraendert":
            continue  # unveraendert ist der Normalfall, verdient kein Badge
        label, farbe = STATUS_LABELS.get(status, (status.upper(), FARBE_TEXT_MUTED))
        badges.append(
            f'<span style="display:inline-block; background:{farbe}15; color:{farbe}; '
            f'font-size:11px; font-weight:600; letter-spacing:0.03em; padding:3px 8px; '
            f'border-radius:3px; margin-right:6px;">{label}</span>'
        )
    return "".join(badges)


def _format_objekt_html(objekt: dict) -> str:
    titel = objekt.get("titel", "(ohne Titel)")
    url = objekt.get("url", "#")
    quelle = objekt.get("quelle", "")
    bild_url = objekt.get("bild_url") or PLATZHALTER_BILD_SVG_DATA_URI

    preisaenderung_html = ""
    if objekt.get("preisaenderung"):
        pa = objekt["preisaenderung"]
        diff_fmt = f"{pa['differenz']:,.0f} €".replace(",", ".")
        preisaenderung_html = (
            f'<div style="color:#B7440E; font-size:12px; font-weight:600; margin-top:2px;">'
            f'&#8595; -{diff_fmt} / -{pa["prozent"]}% seit letztem Scan</div>'
        )

    zimmer = objekt.get("zimmer")
    flaeche = objekt.get("flaeche_qm")
    baujahr = objekt.get("baujahr")
    eek = objekt.get("energieeffizienzklasse")
    termin = objekt.get("termin")

    meta_teile = []
    if flaeche:
        meta_teile.append(f"{flaeche:g} m²")
    if flaeche and objekt.get("kaufpreis"):
        meta_teile.append(f"{objekt['kaufpreis'] / flaeche:,.0f} €/m²".replace(",", "."))
    if zimmer:
        meta_teile.append(f"{zimmer:g} Zi.")
    if baujahr:
        meta_teile.append(f"Bj. {baujahr}")
    meta_teile.append(f"EEK {eek}" if eek else "EEK unbekannt")
    if termin:
        meta_teile.append(f"Termin: {termin}")
    meta_zeile = " &nbsp;·&nbsp; ".join(meta_teile)

    warnungen = objekt.get("warnungen") or []
    warnungen_html = ""
    if warnungen:
        warnungen_html = (
            f'<div style="color:{FARBE_WARNUNG}; font-size:11px; margin-top:6px;">'
            f"&#9888; {', '.join(warnungen)}</div>"
        )

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="margin-bottom:14px; background:#FFFFFF; border:1px solid #E4E0D8;">
      <tr>
        <td width="120" valign="top" style="padding:14px;">
          <img src="{bild_url}" width="100" height="75" alt=""
               style="display:block; width:100px; height:75px; object-fit:cover; border:1px solid #E4E0D8;">
        </td>
        <td valign="top" style="padding:14px 14px 14px 0;">
          <div style="font-size:10px; letter-spacing:0.05em; color:{FARBE_TEXT_MUTED}; text-transform:uppercase; margin-bottom:4px;">
            {quelle}
          </div>
          <div style="margin-bottom:4px;">{_status_badges_html(objekt)}</div>
          <a href="{url}" style="font-family:Georgia,'Times New Roman',serif; font-size:15px; font-weight:700; color:{FARBE_NAVY}; text-decoration:none; line-height:1.35;">
            {titel}
          </a>
          <div style="font-family:Georgia,'Times New Roman',serif; font-size:16px; font-weight:700; color:{FARBE_GOLD}; margin-top:6px;">
            {_format_preis(objekt)}
          </div>
          {preisaenderung_html}
          <div style="font-size:12px; color:{FARBE_TEXT}; margin-top:6px;">{meta_zeile}</div>
          {warnungen_html}
        </td>
      </tr>
    </table>
    """


def _bereich_html(titel: str, objekte: list[dict]) -> str:
    header = f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:28px 0 14px 0;">
      <tr>
        <td style="border-bottom:2px solid {FARBE_NAVY};padding-bottom:6px;">
          <span style="font-family:Georgia,'Times New Roman',serif; font-size:17px; font-weight:700; color:{FARBE_NAVY};">{titel}</span>
          <span style="font-size:12px; color:{FARBE_TEXT_MUTED}; margin-left:8px;">({len(objekte)})</span>
        </td>
      </tr>
    </table>
    """
    if not objekte:
        return header + f'<div style="color:{FARBE_TEXT_MUTED}; font-size:13px; font-style:italic; padding:4px 0 8px;">Keine Treffer in diesem Segment.</div>'
    objekte_sortiert = sorted(
        objekte,
        key=lambda o: ("preisreduktion" not in o.get("status", []), "neu" not in o.get("status", [])),
    )
    return header + "".join(_format_objekt_html(o) for o in objekte_sortiert)


def baue_email_html(stadt_objekte: list[dict], land_objekte: list[dict]) -> tuple[str, str]:
    alle = stadt_objekte + land_objekte
    neue = [o for o in alle if "neu" in o.get("status", [])]
    preisreduktionen = [o for o in alle if "preisreduktion" in o.get("status", [])]

    heute_kurz = date.today().strftime("%d.%m.%Y")
    heute_lang = date.today().strftime("%d. %B %Y")
    betreff = f"Immo-Scan {heute_kurz}: {len(neue)} neue Treffer (Stadt: {len(stadt_objekte)} | Land: {len(land_objekte)})"

    top_pick = neue[0] if neue else (alle[0] if alle else None)
    top_pick_html = (
        f'<a href="{top_pick["url"]}" style="color:{FARBE_GOLD}; text-decoration:none; font-weight:600;">{top_pick.get("titel", "(ohne Titel)")}</a>'
        if top_pick else '<span style="color:' + FARBE_TEXT_MUTED + ';">Keine Objekte in diesem Durchlauf.</span>'
    )

    preisreduktion_block = ""
    if preisreduktionen:
        preisreduktion_block = _bereich_html("Preisreduktionen heute", preisreduktionen)

    html_body = f"""
    <html>
    <body style="margin:0; padding:0; background:{FARBE_HINTERGRUND}; font-family:Arial,Helvetica,sans-serif; color:{FARBE_TEXT};">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{FARBE_HINTERGRUND}; padding:24px 0;">
        <tr>
          <td align="center">
            <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="background:#FFFFFF; border:1px solid #E4E0D8;">

              <!-- Briefkopf -->
              <tr>
                <td style="background:{FARBE_NAVY}; padding:28px 32px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td>
                        <div style="font-family:Georgia,'Times New Roman',serif; font-size:22px; font-weight:700; color:#FFFFFF; letter-spacing:0.04em;">
                          DN&nbsp;REAL&nbsp;ESTATE
                        </div>
                        <div style="font-size:11px; color:{FARBE_GOLD}; letter-spacing:0.12em; text-transform:uppercase; margin-top:2px;">
                          Marktbeobachtung Augsburg
                        </div>
                      </td>
                      <td align="right" style="vertical-align:top;">
                        <div style="font-size:12px; color:#C9CDD4;">{heute_lang}</div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <!-- Anschreiben -->
              <tr>
                <td style="padding:28px 32px 8px 32px;">
                  <div style="font-size:13px; color:{FARBE_TEXT_MUTED}; line-height:1.6;">
                    Guten Tag,<br>
                    anbei die tagesaktuelle Übersicht relevanter Objekte für Ihr Suchprofil in Augsburg Stadt und Landkreis.
                  </div>
                </td>
              </tr>

              <!-- Zusammenfassung -->
              <tr>
                <td style="padding:12px 32px 0 32px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                         style="background:{FARBE_GOLD_HELL}30; border-left:3px solid {FARBE_GOLD};">
                    <tr><td style="padding:14px 16px;">
                      <span style="font-size:12px; color:{FARBE_TEXT};">
                        <strong>{len(alle)}</strong> Objekte gescannt &nbsp;·&nbsp;
                        <strong>{len(neue)}</strong> neu &nbsp;·&nbsp;
                        <strong>{len(preisreduktionen)}</strong> Preisreduktion(en)<br>
                        <span style="color:{FARBE_TEXT_MUTED};">Top-Pick heute:</span> {top_pick_html}
                      </span>
                    </td></tr>
                  </table>
                </td>
              </tr>

              <!-- Objektlisten -->
              <tr>
                <td style="padding:0 32px 8px 32px;">
                  {_bereich_html("Augsburg Stadt", stadt_objekte)}
                  {_bereich_html("Augsburg Land", land_objekte)}
                  {preisreduktion_block}
                </td>
              </tr>

              <!-- Fußzeile -->
              <tr>
                <td style="background:{FARBE_NAVY}; padding:20px 32px; margin-top:12px;">
                  <div style="font-family:Georgia,'Times New Roman',serif; font-size:13px; color:#FFFFFF; font-weight:700;">DN Real Estate</div>
                  <div style="font-size:11px; color:#9AA3B0; margin-top:4px; line-height:1.6;">
                    Automatisiert erstellte Marktbeobachtung, kein Angebot im rechtlichen Sinne.
                    Alle Angaben ohne Gewähr, Prüfung der Originalquelle vor jeder Entscheidung empfohlen.
                  </div>
                </td>
              </tr>

            </table>
          </td>
        </tr>
      </table>
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
    msg["From"] = f"DN Real Estate <{absender}>"
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
