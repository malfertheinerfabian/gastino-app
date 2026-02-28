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
        # AI Provider Config
        AI_PROVIDER=os.getenv("AI_PROVIDER", "anthropic"),
        AI_API_KEY=os.getenv("AI_API_KEY"),
        AI_MODEL=os.getenv("AI_MODEL"),
        AI_BASE_URL=os.getenv("AI_BASE_URL"),
        # Legacy Anthropic Config (fallback)
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY"),
        CLAUDE_MODEL=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250514"),
        # WhatsApp
        WHATSAPP_TOKEN=os.getenv("WHATSAPP_TOKEN"),
        WHATSAPP_VERIFY_TOKEN=os.getenv("WHATSAPP_VERIFY_TOKEN", "gastino-verify-2026"),
        # Telegram
        TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN"),
        TELEGRAM_DEBUG=os.getenv("TELEGRAM_DEBUG", "true").lower() == "true",
        APP_URL=os.getenv("APP_URL"),
        # General
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY"),
        MAX_CONVERSATION_HISTORY=int(os.getenv("MAX_CONVERSATION_HISTORY", "20")),
        ORDER_CONFIRMATION_EMOJI="✅",
    )

    from models.database import init_db
    init_db(app)

    # CORS for dashboard
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
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
