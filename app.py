"""
Gastino.ai - Flask Application Factory
"""
import os
import logging
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("gastino")


def create_app():
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
        DATABASE_URL=os.getenv("DATABASE_URL", "sqlite:///gastino.db"),
        AI_PROVIDER=os.getenv("AI_PROVIDER", "anthropic"),
        AI_API_KEY=os.getenv("AI_API_KEY"),
        AI_MODEL=os.getenv("AI_MODEL"),
        AI_BASE_URL=os.getenv("AI_BASE_URL"),
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY"),
        CLAUDE_MODEL=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250514"),
        WHATSAPP_TOKEN=os.getenv("WHATSAPP_TOKEN"),
        WHATSAPP_VERIFY_TOKEN=os.getenv("WHATSAPP_VERIFY_TOKEN", "gastino-verify-2026"),
        TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
        TELEGRAM_DEBUG=os.getenv("TELEGRAM_DEBUG", "true").lower() == "true",
        APP_URL=os.getenv("APP_URL"),
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY"),
        MAX_CONVERSATION_HISTORY=int(os.getenv("MAX_CONVERSATION_HISTORY", "20")),
        ORDER_CONFIRMATION_EMOJI="✅",
    )

    from models.database import init_db
    init_db(app)

    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    from webhook import webhook_bp
    app.register_blueprint(webhook_bp)

    from routes import api_bp
    app.register_blueprint(api_bp, url_prefix="/api")

    from routes_restaurant import restaurant_bp
    app.register_blueprint(restaurant_bp, url_prefix="/api")

    from telegram_bot import telegram_bp
    app.register_blueprint(telegram_bp)

    @app.route("/health")
    def health():
        provider = app.config.get("AI_PROVIDER", "anthropic")
        model = app.config.get("AI_MODEL") or app.config.get("CLAUDE_MODEL", "?")
        return {"status": "ok", "service": "gastino.ai", "ai": f"{provider}/{model}"}, 200

    logger.info("Gastino.ai started successfully")

    with app.app_context():
        from models.database import Tenant
        if Tenant.query.count() == 0:
            logger.info("Leere DB erkannt - erstelle Testdaten...")
            _auto_seed()

    return app


def _auto_seed():
    from datetime import date, time, timedelta
    from models.database import db, Tenant, Department
    from core.restaurant_engine import (
        RestaurantTable, ReservationExtended, setup_restaurant_defaults
    )

    tenant = Tenant(
        name="Ristorante Sonnenhof",
        type="restaurant",
        whatsapp_number="+39 0471 000000",
        whatsapp_phone_id="test_phone_id",
        languages=["de", "it", "en"],
        plan="trial",
        system_context="Ristorante Sonnenhof - Traditionelle Suedtiroler Kueche. Lage: Meran. Di-So, Mittag 11:30-14:00, Abend 18:00-22:00. Montag Ruhetag. 10 Tische, 46 Plaetze. Hauptgerichte 16-28 Euro. Parken kostenlos. WLAN: Sonnenhof-Guest / willkommen2026",
        menu_context="Speckbrettl 14, Vitello Tonnato 13, Wiener Schnitzel 18, Tafelspitz 24, Hirschragout 26, Risotto Porcini 20, Spinatknoedel 16, Kaesespaetzle 15, Apfelstrudel 9, Panna Cotta 8. Aperol Spritz 8, Hugo 8, Hauswein 5/Glas, Bier 5, Espresso 2.50",
        faq_context="Kreditkarten ja. 3 Kinderstuehle. Terrasse ueberdacht. Vegetarisch/Vegan ja. Gruppen bis 10 in Stube. Mittagsmenue Di-Sa 22 Euro."
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

    sample = [
        {"date": today, "time": time(19, 0), "end_time": time(20, 30), "party_size": 4,
         "guest_name": "Hofer Familie", "table_id": tables.get("Tisch 5"),
         "status": "confirmed", "source": "whatsapp", "language": "de", "notes": "Geburtstag!"},
        {"date": today, "time": time(19, 30), "end_time": time(21, 0), "party_size": 2,
         "guest_name": "Sig. Rossi", "table_id": tables.get("Tisch 1"),
         "status": "confirmed", "source": "whatsapp", "language": "it"},
        {"date": tomorrow, "time": time(19, 0), "end_time": time(20, 30), "party_size": 8,
         "guest_name": "Teamessen Sparkasse", "table_id": tables.get("Tisch 6"),
         "status": "confirmed", "source": "phone", "language": "de"},
    ]
    for r in sample:
        db.session.add(ReservationExtended(tenant_id=tenant.id, **r))
    db.session.commit()

    logger.info("Auto-seed fertig")


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
