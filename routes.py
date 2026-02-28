"""
Gastino.ai — API Routes
REST API für Dashboard, Tenant-Management und Onboarding.
"""
import logging
from flask import Blueprint, request, jsonify
from models.database import db, Tenant, Department, Guest, Order, Reservation, Conversation, Message

logger = logging.getLogger("gastino.api")
api_bp = Blueprint("api", __name__)


# --- TENANT MANAGEMENT ---

@api_bp.route("/tenants", methods=["GET"])
def list_tenants():
    """Alle Betriebe auflisten."""
    tenants = Tenant.query.filter_by(active=True).all()
    return jsonify([{
        "id": t.id,
        "name": t.name,
        "type": t.type,
        "plan": t.plan,
    } for t in tenants])


@api_bp.route("/tenants", methods=["POST"])
def create_tenant():
    """Neuen Betrieb anlegen (Onboarding)."""
    data = request.json

    tenant = Tenant(
        name=data["name"],
        type=data["type"],
        whatsapp_number=data["whatsapp_number"],
        whatsapp_phone_id=data["whatsapp_phone_id"],
        languages=data.get("languages", ["de", "it"]),
        system_context=data.get("system_context", ""),
        menu_context=data.get("menu_context", ""),
        faq_context=data.get("faq_context", ""),
    )
    db.session.add(tenant)
    db.session.commit()

    logger.info(f"Neuer Tenant: {tenant.name} ({tenant.type})")
    return jsonify({"id": tenant.id, "name": tenant.name}), 201


@api_bp.route("/tenants/<tenant_id>", methods=["GET"])
def get_tenant(tenant_id):
    """Betriebsdetails abrufen."""
    tenant = Tenant.query.get_or_404(tenant_id)
    return jsonify({
        "id": tenant.id,
        "name": tenant.name,
        "type": tenant.type,
        "plan": tenant.plan,
        "languages": tenant.languages,
        "active": tenant.active,
        "created_at": tenant.created_at.isoformat(),
    })


@api_bp.route("/tenants/<tenant_id>/context", methods=["PUT"])
def update_context(tenant_id):
    """Knowledge Base / Kontext aktualisieren."""
    tenant = Tenant.query.get_or_404(tenant_id)
    data = request.json

    if "system_context" in data:
        tenant.system_context = data["system_context"]
    if "menu_context" in data:
        tenant.menu_context = data["menu_context"]
    if "faq_context" in data:
        tenant.faq_context = data["faq_context"]

    db.session.commit()
    return jsonify({"status": "updated"})


# --- DEPARTMENTS ---

@api_bp.route("/tenants/<tenant_id>/departments", methods=["POST"])
def create_department(tenant_id):
    """Abteilung anlegen (z.B. Bar, Küche, Rezeption)."""
    data = request.json

    dept = Department(
        tenant_id=tenant_id,
        name=data["name"],
        display_name=data.get("display_name"),
        whatsapp_group_id=data.get("whatsapp_group_id"),
        hours_json=data.get("hours", []),
        is_escalation=data.get("is_escalation", False),
    )
    db.session.add(dept)
    db.session.commit()

    return jsonify({"id": dept.id, "name": dept.name}), 201


@api_bp.route("/tenants/<tenant_id>/departments", methods=["GET"])
def list_departments(tenant_id):
    """Alle Abteilungen eines Betriebs."""
    depts = Department.query.filter_by(tenant_id=tenant_id, active=True).all()
    return jsonify([{
        "id": d.id,
        "name": d.name,
        "display_name": d.display_name,
        "has_whatsapp": bool(d.whatsapp_group_id),
        "is_escalation": d.is_escalation,
        "is_open": d.is_open_now(),
    } for d in depts])


# --- ORDERS ---

@api_bp.route("/tenants/<tenant_id>/orders", methods=["GET"])
def list_orders(tenant_id):
    """Bestellungen auflisten (für Dashboard)."""
    status = request.args.get("status")
    limit = request.args.get("limit", 50, type=int)

    query = Order.query.filter_by(tenant_id=tenant_id)
    if status:
        query = query.filter_by(status=status)

    orders = query.order_by(Order.created_at.desc()).limit(limit).all()

    return jsonify([{
        "id": o.id,
        "type": o.type,
        "items": o.items,
        "room_number": o.room_number,
        "table_number": o.table_number,
        "status": o.status,
        "created_at": o.created_at.isoformat(),
        "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
    } for o in orders])


# --- STATS ---

@api_bp.route("/tenants/<tenant_id>/stats", methods=["GET"])
def tenant_stats(tenant_id):
    """Dashboard-Statistiken."""
    from datetime import datetime, timedelta, date
    from sqlalchemy import func

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)

    # Nachrichten heute
    msgs_today = (
        db.session.query(func.count(Message.id))
        .join(Conversation)
        .filter(
            Conversation.tenant_id == tenant_id,
            Message.created_at >= today
        ).scalar()
    )

    # Nachrichten diese Woche
    msgs_week = (
        db.session.query(func.count(Message.id))
        .join(Conversation)
        .filter(
            Conversation.tenant_id == tenant_id,
            Message.created_at >= week_ago
        ).scalar()
    )

    # Aktive Gäste
    active_guests = Guest.query.filter_by(tenant_id=tenant_id).count()

    # Offene Bestellungen
    pending_orders = Order.query.filter_by(
        tenant_id=tenant_id, status="pending"
    ).count()

    # Reservierungen heute
    reservations_today = Reservation.query.filter_by(
        tenant_id=tenant_id, date=date.today(), status="confirmed"
    ).count()

    # Sprach-Verteilung
    lang_stats = (
        db.session.query(Guest.language, func.count(Guest.id))
        .filter_by(tenant_id=tenant_id)
        .group_by(Guest.language)
        .all()
    )

    return jsonify({
        "messages_today": msgs_today,
        "messages_week": msgs_week,
        "active_guests": active_guests,
        "pending_orders": pending_orders,
        "reservations_today": reservations_today,
        "languages": {lang: count for lang, count in lang_stats},
    })


# --- GUEST MANAGEMENT ---

@api_bp.route("/tenants/<tenant_id>/guests/<guest_id>/room", methods=["PUT"])
def assign_room(tenant_id, guest_id):
    """Zimmer einem Gast zuweisen (manuelles Onboarding)."""
    guest = Guest.query.filter_by(id=guest_id, tenant_id=tenant_id).first_or_404()
    data = request.json

    guest.room_number = data.get("room_number")
    guest.checkin_date = data.get("checkin_date")
    guest.checkout_date = data.get("checkout_date")
    guest.name = data.get("name", guest.name)

    db.session.commit()
    return jsonify({"status": "updated", "room": guest.room_number})
