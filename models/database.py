"""
Gastino.ai — Database Models (SQLAlchemy)
Multi-Tenant Schema für Hotels, Restaurants, FeWos, Bars
"""
import uuid
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db(app):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["DATABASE_URL"]
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        # Import all models so create_all() creates all tables
        from core.restaurant_engine import (
            RestaurantTable, ServicePeriod, ClosedDay, ReservationExtended
        )
        db.create_all()


def new_id():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


# ─── TENANT (Betrieb) ──────────────────────────────────

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(50), nullable=False)  # hotel|restaurant|ferienwohnung|bar
    whatsapp_number = db.Column(db.String(50), unique=True, nullable=False)
    whatsapp_phone_id = db.Column(db.String(100), unique=True, nullable=False)
    languages = db.Column(db.JSON, default=["de", "it"])
    timezone = db.Column(db.String(50), default="Europe/Rome")
    stripe_customer_id = db.Column(db.String(100))
    plan = db.Column(db.String(20), default="trial")
    active = db.Column(db.Boolean, default=True)

    # Knowledge base context — was der Bot über den Betrieb weiß
    system_context = db.Column(db.Text)  # Freitext: Zimmer, Preise, Öffnungszeiten, etc.
    menu_context = db.Column(db.Text)    # Speisekarte / Getränkekarte
    faq_context = db.Column(db.Text)     # Häufige Fragen

    created_at = db.Column(db.DateTime, default=utcnow)

    departments = db.relationship("Department", backref="tenant", lazy="dynamic")
    guests = db.relationship("Guest", backref="tenant", lazy="dynamic")

    def get_full_context(self):
        """Baut den kompletten Kontext für Claude zusammen."""
        parts = [f"Betrieb: {self.name} (Typ: {self.type})"]
        parts.append(f"Sprachen: {', '.join(self.languages)}")
        if self.system_context:
            parts.append(f"\n--- Betriebsinformationen ---\n{self.system_context}")
        if self.menu_context:
            parts.append(f"\n--- Speise-/Getränkekarte ---\n{self.menu_context}")
        if self.faq_context:
            parts.append(f"\n--- Häufige Fragen ---\n{self.faq_context}")

        # Abteilungen auflisten
        depts = Department.query.filter_by(tenant_id=self.id, active=True).all()
        if depts:
            dept_list = ", ".join([d.name for d in depts])
            parts.append(f"\nVerfügbare Abteilungen: {dept_list}")

        return "\n".join(parts)


# ─── DEPARTMENT (Abteilung) ─────────────────────────────

class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)  # "bar", "küche", "rezeption"
    display_name = db.Column(db.String(200))  # "Bar & Lounge"
    whatsapp_group_id = db.Column(db.String(100))  # WhatsApp Gruppen-ID
    hours_json = db.Column(db.JSON)  # [{"start": "10:00", "end": "23:00"}]
    fallback_dept_id = db.Column(db.String(36), db.ForeignKey("departments.id"))
    is_escalation = db.Column(db.Boolean, default=False)
    active = db.Column(db.Boolean, default=True)

    def is_open_now(self):
        """Prüft ob die Abteilung gerade geöffnet ist."""
        if not self.hours_json:
            return True  # Keine Zeiten definiert = immer offen
        from datetime import datetime
        import pytz
        tz = pytz.timezone(self.tenant.timezone if self.tenant else "Europe/Rome")
        now = datetime.now(tz)
        current_time = now.strftime("%H:%M")
        for slot in self.hours_json:
            if slot.get("start", "00:00") <= current_time <= slot.get("end", "23:59"):
                return True
        return False


# ─── GUEST (Gast) ──────────────────────────────────────

class Guest(db.Model):
    __tablename__ = "guests"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    whatsapp_id = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200))
    language = db.Column(db.String(5), default="de")
    room_number = db.Column(db.String(20))
    table_number = db.Column(db.String(20))
    checkin_date = db.Column(db.Date)
    checkout_date = db.Column(db.Date)
    tags = db.Column(db.JSON, default=[])
    created_at = db.Column(db.DateTime, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "whatsapp_id", name="uq_tenant_guest"),
    )

    conversations = db.relationship("Conversation", backref="guest", lazy="dynamic")


# ─── CONVERSATION ──────────────────────────────────────

class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    guest_id = db.Column(db.String(36), db.ForeignKey("guests.id"), nullable=False)
    status = db.Column(db.String(20), default="active")
    last_intent = db.Column(db.String(50))
    pending_entities = db.Column(db.JSON, default=dict)  # Gesammelte Entities über mehrere Nachrichten
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    messages = db.relationship("Message", backref="conversation", lazy="dynamic",
                               order_by="Message.created_at")


# ─── MESSAGE ──────────────────────────────────────────

class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    conversation_id = db.Column(db.String(36), db.ForeignKey("conversations.id"), nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # inbound|outbound
    sender_type = db.Column(db.String(10), nullable=False)  # guest|ai|staff
    content = db.Column(db.Text, nullable=False)
    metadata_json = db.Column(db.JSON)  # {intent, confidence, tokens_used}
    created_at = db.Column(db.DateTime, default=utcnow)


# ─── ORDER (Bestellungen / Roomservice) ────────────────

class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    guest_id = db.Column(db.String(36), db.ForeignKey("guests.id"), nullable=False)
    department_id = db.Column(db.String(36), db.ForeignKey("departments.id"))
    type = db.Column(db.String(50), nullable=False)  # roomservice|table_order|reservation
    items = db.Column(db.JSON, nullable=False)  # [{"name": "Aperol Spritz", "qty": 2, "notes": ""}]
    room_number = db.Column(db.String(20))
    table_number = db.Column(db.String(20))
    status = db.Column(db.String(20), default="pending")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
    confirmed_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)

    guest = db.relationship("Guest", backref="orders")
    department = db.relationship("Department", backref="orders")


# ─── RESERVATION ──────────────────────────────────────

class Reservation(db.Model):
    __tablename__ = "reservations"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    guest_id = db.Column(db.String(36), db.ForeignKey("guests.id"))
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    party_size = db.Column(db.Integer, nullable=False)
    guest_name = db.Column(db.String(200))
    status = db.Column(db.String(20), default="confirmed")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow)
