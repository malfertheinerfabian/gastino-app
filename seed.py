"""
Gastino.ai - Seed Script
"""
import os
import sys
from datetime import date, time, timedelta

from app import create_app
app = create_app()

with app.app_context():
    from models.database import db, Tenant, Department, Guest
    from core.restaurant_engine import (
        RestaurantTable, ServicePeriod, ClosedDay,
        ReservationExtended, setup_restaurant_defaults
    )

    print("Erstelle Tabellen...")
    db.create_all()

    print("Loesche alte Daten...")
    for tbl in ["reservations_v2","service_periods","closed_days","restaurant_tables","reservations","orders","messages","conversations","departments","guests","tenants"]:
        try:
            db.session.execute(db.text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.session.commit()

    print("Erstelle Test-Restaurant...")
    tenant = Tenant(
        name="Ristorante Sonnenhof",
        type="restaurant",
        whatsapp_number="+39 0471 000000",
        whatsapp_phone_id="test_phone_id",
        languages=["de", "it", "en"],
        plan="trial",
        system_context="""Ristorante Sonnenhof - Traditionelle Suedtiroler Kueche mit italienischem Einfluss.
Lage: Meran, Suedtirol. Oeffnungszeiten: Di-So, Mittagessen 11:30-14:00, Abendessen 18:00-22:00. Montag Ruhetag.
10 Tische, ca. 46 Plaetze. Preise Hauptgerichte: 16-28 Euro.
Parken kostenlos hinter dem Restaurant. WLAN: Sonnenhof-Guest / Passwort: willkommen2026""",
        menu_context="""ABENDKARTE:
Vorspeisen: Suedtiroler Speckbrettl 14 Euro, Vitello Tonnato 13 Euro
Hauptgerichte: Wiener Schnitzel 18 Euro, Tafelspitz 24 Euro, Hirschragout mit Polenta 26 Euro,
Risotto ai Funghi Porcini 20 Euro, Spinatknoedel mit Salbeibutter 16 Euro, Kaesespaetzle 15 Euro
Desserts: Apfelstrudel mit Vanilleeis 9 Euro, Panna Cotta 8 Euro
GETRAENKE: Aperol Spritz 8 Euro, Hugo 8 Euro, Hauswein 5 Euro/Glas, Bier 0.5l 5 Euro,
Espresso 2.50 Euro, Mineralwasser 0.75l 4 Euro. Vegetarische und glutenfreie Optionen verfuegbar.""",
        faq_context="""Kreditkarten: Ja (Visa, Mastercard, EC). Kinderstuhl: 3 verfuegbar.
Terrasse teilweise ueberdacht. Vegetarisch/Vegan: Ja. Grosse Gruppen bis 10 in der Stube.
Mittagsmenue Di-Sa 3-Gaenge 22 Euro."""
    )
    db.session.add(tenant)
    db.session.commit()

    for d in [
        {"name": "kueche", "display_name": "Kueche", "is_escalation": False},
        {"name": "bar", "display_name": "Bar", "is_escalation": False},
        {"name": "service", "display_name": "Service", "is_escalation": True},
    ]:
        db.session.add(Department(tenant_id=tenant.id, **d))
    db.session.commit()

    setup_restaurant_defaults(tenant.id, {"closed_day": 0, "dinner_duration": 90, "tables": [
        {"name": "Tisch 1", "zone": "innen", "min": 2, "max": 2, "priority": 1},
        {"name": "Tisch 2", "zone": "innen", "min": 2, "max": 2, "priority": 1},
        {"name": "Tisch 3", "zone": "innen", "min": 2, "max": 4, "priority": 3},
        {"name": "Tisch 4", "zone": "innen", "min": 2, "max": 4, "priority": 3},
        {"name": "Tisch 5", "zone": "innen", "min": 4, "max": 6, "priority": 5},
        {"name": "Tisch 6", "zone": "stube", "min": 4, "max": 8, "priority": 7},
        {"name": "Tisch 7", "zone": "stube", "min": 6, "max": 10, "priority": 8},
        {"name": "Terrasse 1", "zone": "terrasse", "min": 2, "max": 4, "priority": 2},
        {"name": "Terrasse 2", "zone": "terrasse", "min": 2, "max": 4, "priority": 2},
        {"name": "Terrasse 3", "zone": "terrasse", "min": 4, "max": 6, "priority": 4},
    ]})

    today = date.today()
    tomorrow = today + timedelta(days=1)
    tables = {t.name: t.id for t in RestaurantTable.query.filter_by(tenant_id=tenant.id).all()}

    for r in [
        {"date": today, "time": time(19,0), "end_time": time(20,30), "party_size": 4,
         "guest_name": "Hofer Familie", "table_id": tables.get("Tisch 5"),
         "status": "confirmed", "source": "whatsapp", "language": "de", "notes": "Geburtstag!"},
        {"date": today, "time": time(19,30), "end_time": time(21,0), "party_size": 2,
         "guest_name": "Sig. Rossi", "table_id": tables.get("Tisch 1"),
         "status": "confirmed", "source": "whatsapp", "language": "it"},
        {"date": today, "time": time(20,0), "end_time": time(21,30), "party_size": 6,
         "guest_name": "Mair Geburtstagsfeier", "table_id": tables.get("Tisch 7"),
         "status": "confirmed", "source": "phone", "language": "de"},
        {"date": tomorrow, "time": time(19,0), "end_time": time(20,30), "party_size": 8,
         "guest_name": "Teamessen Sparkasse", "table_id": tables.get("Tisch 6"),
         "status": "confirmed", "source": "phone", "language": "de"},
    ]:
        db.session.add(ReservationExtended(tenant_id=tenant.id, **r))
    db.session.commit()

    t = RestaurantTable.query.filter_by(tenant_id=tenant.id).count()
    p = ServicePeriod.query.filter_by(tenant_id=tenant.id).count()
    r = ReservationExtended.query.filter_by(tenant_id=tenant.id).count()

    print(f"""
=============================================
  GASTINO TEST-DATEN ERSTELLT
=============================================
  Betrieb:   Ristorante Sonnenhof
  Tische:    {t}
  Perioden:  {p}
  Reserv.:   {r}
  Ruhetag:   Montag
  Tenant-ID: {tenant.id}
=============================================

  1. ngrok http 5000
  2. APP_URL in .env eintragen
  3. python app.py
  4. Webhook setzen (im Browser):
     https://api.telegram.org/bot{{TOKEN}}/setWebhook?url={{APP_URL}}/telegram/webhook
  5. Dem Bot /start schreiben!
""")
