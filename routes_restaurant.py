"""
Gastino.ai - Restaurant Reservation API Routes
REST API faeuer Tischverwaltung, Reservierungen, Tagesplanung.
"""
import logging
from datetime import date, time, datetime
from flask import Blueprint, request, jsonify

from models.database import db
from core.restaurant_engine import (
    ReservationEngine, RestaurantTable, ServicePeriod,
    ClosedDay, ReservationExtended, setup_restaurant_defaults
)

logger = logging.getLogger("gastino.restaurant_api")
restaurant_bp = Blueprint("restaurant", __name__)


# ------ VERFaeUeGBARKEIT ----------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/availability", methods=["GET"])
def check_availability(tid):
    """Verfaeuegbare Slots faeuer ein Datum + Personenanzahl."""
    target_date = request.args.get("date", date.today().isoformat())
    party_size = request.args.get("party_size", 2, type=int)

    engine = ReservationEngine(tid)
    slots = engine.get_available_slots(
        target_date=date.fromisoformat(target_date),
        party_size=party_size,
    )
    return jsonify({"date": target_date, "party_size": party_size, "slots": slots})


@restaurant_bp.route("/tenants/<tid>/availability/check", methods=["POST"])
def check_specific_availability(tid):
    """Praeueft ob ein spezifischer Zeitpunkt verfaeuegbar ist."""
    data = request.json
    engine = ReservationEngine(tid)

    result = engine.check_availability(
        target_date=date.fromisoformat(data["date"]),
        target_time=time.fromisoformat(data["time"]),
        party_size=data["party_size"],
    )
    return jsonify(result)


# ------ RESERVIERUNGEN --------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/reservations", methods=["POST"])
def create_reservation(tid):
    """Neue Reservierung erstellen."""
    data = request.json
    engine = ReservationEngine(tid)

    result = engine.create_reservation(
        target_date=date.fromisoformat(data["date"]),
        target_time=time.fromisoformat(data["time"]),
        party_size=data["party_size"],
        guest_name=data["guest_name"],
        guest_phone=data.get("guest_phone"),
        language=data.get("language", "de"),
        zone_preference=data.get("zone_preference"),
        notes=data.get("notes"),
        special_requests=data.get("special_requests"),
        source=data.get("source", "dashboard"),
        guest_id=data.get("guest_id"),
    )

    status_code = 201 if result["success"] else 409
    return jsonify(result), status_code


@restaurant_bp.route("/tenants/<tid>/reservations", methods=["GET"])
def list_reservations(tid):
    """Reservierungen auflisten (mit Filtern)."""
    target_date = request.args.get("date")
    status = request.args.get("status")
    limit = request.args.get("limit", 100, type=int)

    try:
        query = ReservationExtended.query.filter_by(tenant_id=tid)
        if target_date:
            query = query.filter_by(date=date.fromisoformat(target_date))
        if status:
            query = query.filter_by(status=status)

        reservations = query.order_by(
            ReservationExtended.date, ReservationExtended.time
        ).limit(limit).all()

        logger.info(f"Reservierungen gefunden: {len(reservations)} (tenant={tid}, date={target_date})")
    except Exception as e:
        logger.error(f"DB-Fehler list_reservations: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    result = []
    for r in reservations:
        try:
            tbl = r.table
            result.append({
                "id": r.id,
                "date": r.date.isoformat(),
                "time": r.time.strftime("%H:%M"),
                "end_time": r.end_time.strftime("%H:%M") if r.end_time else None,
                "party_size": r.party_size,
                "guest_name": r.guest_name,
                "guest_phone": r.guest_phone,
                "language": r.language,
                "table": tbl.name if tbl else None,
                "zone": tbl.zone if tbl else None,
                "status": r.status,
                "source": r.source,
                "notes": r.notes,
                "special_requests": r.special_requests,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        except Exception as e:
            logger.error(f"Fehler bei Reservierung {r.id}: {e}")
            result.append({
                "id": r.id, "date": r.date.isoformat(), "time": r.time.strftime("%H:%M"),
                "end_time": None, "party_size": r.party_size, "guest_name": r.guest_name,
                "guest_phone": r.guest_phone, "language": r.language,
                "table": None, "zone": None, "status": r.status, "source": r.source,
                "notes": r.notes, "special_requests": r.special_requests, "created_at": None,
            })
    return jsonify(result)


@restaurant_bp.route("/tenants/<tid>/reservations/<rid>", methods=["PUT"])
def update_reservation(tid, rid):
    """Reservierung aktualisieren."""
    data = request.json
    res = ReservationExtended.query.filter_by(id=rid, tenant_id=tid).first_or_404()

    if "guest_name" in data:
        res.guest_name = data["guest_name"]
    if "party_size" in data:
        res.party_size = data["party_size"]
    if "notes" in data:
        res.notes = data["notes"]
    if "special_requests" in data:
        res.special_requests = data["special_requests"]
    if "table_id" in data:
        res.table_id = data["table_id"]

    db.session.commit()
    return jsonify({"status": "updated"})


@restaurant_bp.route("/tenants/<tid>/reservations/<rid>/status", methods=["PUT"])
def update_reservation_status(tid, rid):
    """Status einer Reservierung aeaendern (seated, completed, noshow, cancelled)."""
    data = request.json
    new_status = data["status"]
    engine = ReservationEngine(tid)

    actions = {
        "seated": engine.seat_guest,
        "completed": engine.complete_reservation,
        "noshow": engine.mark_noshow,
        "cancelled": engine.cancel_reservation,
    }

    action = actions.get(new_status)
    if not action:
        return jsonify({"error": f"Ungaeueltiger Status: {new_status}"}), 400

    success = action(rid)
    if success:
        return jsonify({"status": new_status})
    return jsonify({"error": "Status-aeUebergang nicht maeoeglich"}), 409


# ------ TAGESANSICHT ------------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/day-overview", methods=["GET"])
def day_overview(tid):
    """Komplette Tagesaeuebersicht."""
    target_date = request.args.get("date", date.today().isoformat())
    engine = ReservationEngine(tid)
    return jsonify(engine.get_day_overview(date.fromisoformat(target_date)))


@restaurant_bp.route("/tenants/<tid>/table-timeline", methods=["GET"])
def table_timeline(tid):
    """Timeline: Welcher Tisch ist wann belegt?"""
    target_date = request.args.get("date", date.today().isoformat())
    engine = ReservationEngine(tid)
    return jsonify(engine.get_table_timeline(date.fromisoformat(target_date)))


# ------ TISCHVERWALTUNG ----------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/tables", methods=["GET"])
def list_tables(tid):
    """Alle Tische auflisten."""
    tables = RestaurantTable.query.filter_by(tenant_id=tid, active=True).order_by(
        RestaurantTable.zone, RestaurantTable.name
    ).all()
    return jsonify([{
        "id": t.id,
        "name": t.name,
        "zone": t.zone,
        "min_seats": t.min_seats,
        "max_seats": t.max_seats,
        "priority": t.priority,
        "is_combinable": t.is_combinable,
        "notes": t.notes,
    } for t in tables])


@restaurant_bp.route("/tenants/<tid>/tables", methods=["POST"])
def create_table(tid):
    """Neuen Tisch anlegen."""
    data = request.json
    table = RestaurantTable(
        tenant_id=tid,
        name=data["name"],
        zone=data.get("zone", "innen"),
        min_seats=data.get("min_seats", 2),
        max_seats=data.get("max_seats", 4),
        priority=data.get("priority", 5),
        is_combinable=data.get("is_combinable", False),
        notes=data.get("notes"),
    )
    db.session.add(table)
    db.session.commit()
    return jsonify({"id": table.id, "name": table.name}), 201


@restaurant_bp.route("/tenants/<tid>/tables/<table_id>", methods=["PUT"])
def update_table(tid, table_id):
    """Tisch bearbeiten."""
    data = request.json
    table = RestaurantTable.query.filter_by(id=table_id, tenant_id=tid).first_or_404()

    for field in ["name", "zone", "min_seats", "max_seats", "priority", "is_combinable", "notes"]:
        if field in data:
            setattr(table, field, data[field])

    db.session.commit()
    return jsonify({"status": "updated"})


@restaurant_bp.route("/tenants/<tid>/tables/<table_id>", methods=["DELETE"])
def delete_table(tid, table_id):
    """Tisch deaktivieren."""
    table = RestaurantTable.query.filter_by(id=table_id, tenant_id=tid).first_or_404()
    table.active = False
    db.session.commit()
    return jsonify({"status": "deactivated"})


# ------ SERVICE-ZEITEN ------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/service-periods", methods=["GET"])
def list_service_periods(tid):
    """Service-Perioden auflisten."""
    periods = ServicePeriod.query.filter_by(tenant_id=tid, active=True).order_by(
        ServicePeriod.day_of_week, ServicePeriod.start_time
    ).all()

    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    return jsonify([{
        "id": p.id,
        "name": p.name,
        "day": days[p.day_of_week],
        "day_of_week": p.day_of_week,
        "start_time": p.start_time.strftime("%H:%M"),
        "end_time": p.end_time.strftime("%H:%M"),
        "last_seating": p.last_seating.strftime("%H:%M") if p.last_seating else None,
        "slot_duration_min": p.slot_duration_min,
        "slot_interval_min": p.slot_interval_min,
    } for p in periods])


@restaurant_bp.route("/tenants/<tid>/service-periods", methods=["POST"])
def create_service_period(tid):
    """Service-Periode erstellen."""
    data = request.json
    period = ServicePeriod(
        tenant_id=tid,
        name=data["name"],
        day_of_week=data["day_of_week"],
        start_time=time.fromisoformat(data["start_time"]),
        end_time=time.fromisoformat(data["end_time"]),
        last_seating=time.fromisoformat(data["last_seating"]) if data.get("last_seating") else None,
        slot_duration_min=data.get("slot_duration_min", 90),
        slot_interval_min=data.get("slot_interval_min", 30),
    )
    db.session.add(period)
    db.session.commit()
    return jsonify({"id": period.id}), 201


# ------ RUHETAGE ------------------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/closed-days", methods=["GET"])
def list_closed_days(tid):
    """Ruhetage auflisten."""
    days = ClosedDay.query.filter_by(tenant_id=tid).all()
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    return jsonify([{
        "id": d.id,
        "date": d.date.isoformat() if d.date else None,
        "recurring_weekday": weekdays[d.recurring_weekday] if d.recurring_weekday is not None else None,
        "reason": d.reason,
    } for d in days])


@restaurant_bp.route("/tenants/<tid>/closed-days", methods=["POST"])
def create_closed_day(tid):
    """Ruhetag/geschlossenen Tag anlegen."""
    data = request.json
    closed = ClosedDay(
        tenant_id=tid,
        date=date.fromisoformat(data["date"]) if data.get("date") else date.today(),
        recurring_weekday=data.get("recurring_weekday"),
        reason=data.get("reason", "Geschlossen"),
    )
    db.session.add(closed)
    db.session.commit()
    return jsonify({"id": closed.id}), 201


# ------ STATISTIKEN ------------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/reservation-stats", methods=["GET"])
def reservation_stats(tid):
    """Reservierungsstatistiken."""
    from_date = request.args.get("from")
    to_date = request.args.get("to")

    engine = ReservationEngine(tid)
    stats = engine.get_stats(
        from_date=date.fromisoformat(from_date) if from_date else None,
        to_date=date.fromisoformat(to_date) if to_date else None,
    )
    return jsonify(stats)


# ------ SETUP ------------------------------------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/setup-restaurant", methods=["POST"])
def setup_restaurant(tid):
    """Richtet ein Restaurant mit Standard-Konfiguration ein."""
    config = request.json or {}
    setup_restaurant_defaults(tid, config)
    return jsonify({"status": "Restaurant eingerichtet", "tenant_id": tid}), 201


# ------ WALK-IN (Laufkunde) ------------------------------------------------------------

@restaurant_bp.route("/tenants/<tid>/walkin", methods=["POST"])
def create_walkin(tid):
    """Schnelle Walk-in Reservierung (Gast steht vor der Taeuer)."""
    data = request.json
    engine = ReservationEngine(tid)

    now = datetime.now()
    result = engine.create_reservation(
        target_date=now.date(),
        target_time=now.time().replace(second=0, microsecond=0),
        party_size=data["party_size"],
        guest_name=data.get("guest_name", "Walk-in"),
        source="walkin",
    )

    if result["success"]:
        # Direkt als seated markieren
        engine.seat_guest(result["reservation"]["id"])
        result["reservation"]["status"] = "seated"

    return jsonify(result), 201 if result["success"] else 409
