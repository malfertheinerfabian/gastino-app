"""
Gastino.ai — Restaurant Reservation Backend
Komplettes Reservierungssystem: Tischverwaltung, Kapazitätsplanung,
Verfügbarkeitsprüfung, automatische Bestätigungen, No-Show-Tracking.
"""
import logging
from datetime import datetime, date, time, timedelta, timezone
from models.database import db, Tenant, Guest, Reservation

logger = logging.getLogger("gastino.reservations")


# ─── TABLE MODEL (neue Tabelle) ────────────────────────

class RestaurantTable(db.Model):
    """Ein physischer Tisch im Restaurant."""
    __tablename__ = "restaurant_tables"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:12])
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)       # "Tisch 1", "Terrasse A3"
    zone = db.Column(db.String(50), default="innen")       # innen|terrasse|stube|garten|bar
    min_seats = db.Column(db.Integer, default=2)
    max_seats = db.Column(db.Integer, default=4)
    is_combinable = db.Column(db.Boolean, default=False)   # Kann mit Nachbartisch kombiniert werden
    combine_with = db.Column(db.String(36))                 # ID des kombinierbaren Tischs
    priority = db.Column(db.Integer, default=5)             # 1=zuerst belegen, 10=zuletzt
    active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)                              # "Am Fenster", "Neben dem Kamin"

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "name", name="uq_table_name"),
    )


class ServicePeriod(db.Model):
    """Definiert wann das Restaurant Reservierungen annimmt."""
    __tablename__ = "service_periods"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:12])
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)        # "Mittagessen", "Abendessen"
    day_of_week = db.Column(db.Integer, nullable=False)     # 0=Montag, 6=Sonntag
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    last_seating = db.Column(db.Time)                       # Letzte Reservierung (z.B. 21:00 bei Schluss 22:00)
    slot_duration_min = db.Column(db.Integer, default=90)   # Wie lang ein Tisch belegt ist
    slot_interval_min = db.Column(db.Integer, default=30)   # Reservierungsintervall (19:00, 19:30, 20:00...)
    max_covers = db.Column(db.Integer)                      # Max Gäste pro Service (optional)
    active = db.Column(db.Boolean, default=True)


class ClosedDay(db.Model):
    """Ruhetage und Feiertage."""
    __tablename__ = "closed_days"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:12])
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(200))                      # "Ruhetag", "Weihnachten", "Betriebsurlaub"
    recurring_weekday = db.Column(db.Integer)                # Wenn gesetzt: jede Woche dieser Tag geschlossen


# ─── ERWEITERTE RESERVATION ────────────────────────────

class ReservationExtended(db.Model):
    """Erweiterte Reservierung mit Tischzuweisung und Tracking."""
    __tablename__ = "reservations_v2"

    id = db.Column(db.String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex[:12])
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    guest_id = db.Column(db.String(36), db.ForeignKey("guests.id"))

    # Reservierungsdetails
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time)                           # Berechnetes Ende
    party_size = db.Column(db.Integer, nullable=False)
    guest_name = db.Column(db.String(200), nullable=False)
    guest_phone = db.Column(db.String(50))
    guest_email = db.Column(db.String(200))
    language = db.Column(db.String(5), default="de")

    # Tischzuweisung
    table_id = db.Column(db.String(36), db.ForeignKey("restaurant_tables.id"))
    zone_preference = db.Column(db.String(50))              # Wunsch: innen|terrasse|etc

    # Status-Tracking
    status = db.Column(db.String(20), default="confirmed")  # confirmed|seated|completed|noshow|cancelled
    source = db.Column(db.String(20), default="whatsapp")   # whatsapp|phone|walkin|website
    notes = db.Column(db.Text)                               # "Geburtstag", "Allergien: Nüsse"
    special_requests = db.Column(db.Text)                    # "Kinderstuhl", "Rollstuhl"

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    confirmed_at = db.Column(db.DateTime)
    reminder_sent_at = db.Column(db.DateTime)
    seated_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)
    noshow_marked_at = db.Column(db.DateTime)

    # Relations
    table = db.relationship("RestaurantTable", backref="reservations")
    guest = db.relationship("Guest", backref="reservations_v2")

    @property
    def is_past(self):
        return self.date < date.today()

    @property
    def duration_minutes(self):
        if self.end_time and self.time:
            dt1 = datetime.combine(date.today(), self.time)
            dt2 = datetime.combine(date.today(), self.end_time)
            return int((dt2 - dt1).total_seconds() / 60)
        return 90  # Default


# ─── RESERVATION ENGINE ────────────────────────────────

class ReservationEngine:
    """
    Kernlogik für das Reservierungssystem.
    Prüft Verfügbarkeit, weist Tische zu, verwaltet Kapazität.
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    # ─── VERFÜGBARKEIT ──────────────────────────────

    def get_available_slots(self, target_date: date, party_size: int) -> list:
        """
        Gibt alle verfügbaren Zeitslots für ein Datum und Gruppengröße zurück.
        Returns: [{"time": "19:00", "tables": ["Tisch 3", "Tisch 7"], "zone": "innen"}, ...]
        """
        # Prüfe ob Restaurant geschlossen ist
        if self._is_closed(target_date):
            return []

        weekday = target_date.weekday()
        periods = ServicePeriod.query.filter_by(
            tenant_id=self.tenant_id, day_of_week=weekday, active=True
        ).all()

        if not periods:
            return []

        available = []
        for period in periods:
            slots = self._generate_time_slots(period)
            for slot_time in slots:
                tables = self._find_available_tables(target_date, slot_time, party_size, period.slot_duration_min)
                if tables:
                    available.append({
                        "time": slot_time.strftime("%H:%M"),
                        "period": period.name,
                        "tables": [{"id": t.id, "name": t.name, "zone": t.zone, "seats": t.max_seats} for t in tables],
                        "best_table": tables[0].name,
                    })

        return available

    def check_availability(self, target_date: date, target_time: time, party_size: int) -> dict:
        """
        Prüft ob ein spezifischer Zeitpunkt verfügbar ist.
        Returns: {"available": True, "table": {...}, "alternatives": [...]}
        """
        if self._is_closed(target_date):
            return {"available": False, "reason": "closed", "alternatives": []}

        # Service-Periode finden
        period = self._find_service_period(target_date, target_time)
        if not period:
            return {"available": False, "reason": "outside_hours", "alternatives": []}

        # Prüfe ob nach Last Seating
        if period.last_seating and target_time > period.last_seating:
            return {"available": False, "reason": "after_last_seating", "alternatives": []}

        # Verfügbare Tische suchen
        tables = self._find_available_tables(target_date, target_time, party_size, period.slot_duration_min)

        if tables:
            return {
                "available": True,
                "table": {"id": tables[0].id, "name": tables[0].name, "zone": tables[0].zone},
                "alternatives": [],
            }

        # Keine Tische frei → Alternativen suchen
        alternatives = self._find_alternative_slots(target_date, party_size, target_time)
        return {
            "available": False,
            "reason": "fully_booked",
            "alternatives": alternatives[:3],
        }

    # ─── RESERVIERUNG ERSTELLEN ─────────────────────

    def create_reservation(self, target_date: date, target_time: time,
                           party_size: int, guest_name: str,
                           guest_phone: str = None, language: str = "de",
                           zone_preference: str = None,
                           notes: str = None, special_requests: str = None,
                           source: str = "whatsapp",
                           guest_id: str = None) -> dict:
        """
        Erstellt eine neue Reservierung mit automatischer Tischzuweisung.
        Returns: {"success": True, "reservation": {...}} oder {"success": False, "error": "..."}
        """
        # Verfügbarkeit prüfen
        availability = self.check_availability(target_date, target_time, party_size)

        if not availability["available"]:
            return {
                "success": False,
                "error": availability["reason"],
                "alternatives": availability.get("alternatives", []),
            }

        # Service-Periode für Slot-Dauer
        period = self._find_service_period(target_date, target_time)
        slot_duration = period.slot_duration_min if period else 90

        # Besten Tisch zuweisen
        table_id = availability["table"]["id"]
        if zone_preference:
            tables = self._find_available_tables(target_date, target_time, party_size, slot_duration)
            preferred = [t for t in tables if t.zone == zone_preference]
            if preferred:
                table_id = preferred[0].id

        # Ende berechnen
        end_dt = datetime.combine(date.today(), target_time) + timedelta(minutes=slot_duration)
        end_time = end_dt.time()

        # Reservierung erstellen
        reservation = ReservationExtended(
            tenant_id=self.tenant_id,
            guest_id=guest_id,
            date=target_date,
            time=target_time,
            end_time=end_time,
            party_size=party_size,
            guest_name=guest_name,
            guest_phone=guest_phone,
            language=language,
            table_id=table_id,
            zone_preference=zone_preference,
            status="confirmed",
            source=source,
            notes=notes,
            special_requests=special_requests,
            confirmed_at=datetime.now(timezone.utc),
        )
        db.session.add(reservation)
        db.session.commit()

        logger.info(f"Reservierung erstellt: {reservation.id} — {target_date} {target_time}, "
                    f"{party_size} Pers., Tisch {availability['table']['name']}")

        return {
            "success": True,
            "reservation": {
                "id": reservation.id,
                "date": target_date.isoformat(),
                "time": target_time.strftime("%H:%M"),
                "party_size": party_size,
                "guest_name": guest_name,
                "table": availability["table"]["name"],
                "zone": availability["table"]["zone"],
            },
        }

    # ─── STATUS MANAGEMENT ──────────────────────────

    def seat_guest(self, reservation_id: str) -> bool:
        """Markiert Gast als gesetzt (angekommen)."""
        res = ReservationExtended.query.get(reservation_id)
        if res and res.status == "confirmed":
            res.status = "seated"
            res.seated_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        return False

    def complete_reservation(self, reservation_id: str) -> bool:
        """Markiert Reservierung als abgeschlossen (Gast gegangen)."""
        res = ReservationExtended.query.get(reservation_id)
        if res and res.status == "seated":
            res.status = "completed"
            res.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        return False

    def mark_noshow(self, reservation_id: str) -> bool:
        """Markiert als No-Show."""
        res = ReservationExtended.query.get(reservation_id)
        if res and res.status == "confirmed":
            res.status = "noshow"
            res.noshow_marked_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f"No-Show markiert: {res.guest_name} ({res.date} {res.time})")
            return True
        return False

    def cancel_reservation(self, reservation_id: str) -> bool:
        """Storniert eine Reservierung."""
        res = ReservationExtended.query.get(reservation_id)
        if res and res.status in ("confirmed",):
            res.status = "cancelled"
            res.cancelled_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        return False

    # ─── TAGESÜBERSICHT ─────────────────────────────

    def get_day_overview(self, target_date: date) -> dict:
        """Komplette Tagesübersicht für Dashboard."""
        reservations = (
            ReservationExtended.query
            .filter_by(tenant_id=self.tenant_id, date=target_date)
            .filter(ReservationExtended.status.in_(["confirmed", "seated"]))
            .order_by(ReservationExtended.time)
            .all()
        )

        tables = RestaurantTable.query.filter_by(tenant_id=self.tenant_id, active=True).all()
        total_seats = sum(t.max_seats for t in tables)
        booked_seats = sum(r.party_size for r in reservations)

        # Gruppiert nach Zeitslot
        by_time = {}
        for r in reservations:
            key = r.time.strftime("%H:%M")
            if key not in by_time:
                by_time[key] = []
            by_time[key].append({
                "id": r.id,
                "guest_name": r.guest_name,
                "party_size": r.party_size,
                "table": r.table.name if r.table else "–",
                "zone": r.table.zone if r.table else "–",
                "status": r.status,
                "notes": r.notes,
                "special_requests": r.special_requests,
                "source": r.source,
                "phone": r.guest_phone,
                "language": r.language,
            })

        return {
            "date": target_date.isoformat(),
            "is_closed": self._is_closed(target_date),
            "total_tables": len(tables),
            "total_seats": total_seats,
            "booked_seats": booked_seats,
            "utilization_pct": round((booked_seats / total_seats * 100) if total_seats else 0),
            "reservation_count": len(reservations),
            "by_time": by_time,
        }

    def get_table_timeline(self, target_date: date) -> list:
        """Timeline-Ansicht: Welcher Tisch ist wann belegt?"""
        tables = RestaurantTable.query.filter_by(
            tenant_id=self.tenant_id, active=True
        ).order_by(RestaurantTable.zone, RestaurantTable.name).all()

        result = []
        for table in tables:
            reservations = (
                ReservationExtended.query
                .filter_by(tenant_id=self.tenant_id, date=target_date, table_id=table.id)
                .filter(ReservationExtended.status.in_(["confirmed", "seated"]))
                .order_by(ReservationExtended.time)
                .all()
            )
            result.append({
                "table_id": table.id,
                "table_name": table.name,
                "zone": table.zone,
                "max_seats": table.max_seats,
                "reservations": [{
                    "id": r.id,
                    "time": r.time.strftime("%H:%M"),
                    "end_time": r.end_time.strftime("%H:%M") if r.end_time else "–",
                    "guest_name": r.guest_name,
                    "party_size": r.party_size,
                    "status": r.status,
                } for r in reservations],
            })

        return result

    # ─── STATISTIKEN ────────────────────────────────

    def get_stats(self, from_date: date = None, to_date: date = None) -> dict:
        """Reservierungsstatistiken."""
        if not from_date:
            from_date = date.today() - timedelta(days=30)
        if not to_date:
            to_date = date.today()

        reservations = (
            ReservationExtended.query
            .filter_by(tenant_id=self.tenant_id)
            .filter(ReservationExtended.date.between(from_date, to_date))
            .all()
        )

        total = len(reservations)
        confirmed = sum(1 for r in reservations if r.status in ("confirmed", "seated", "completed"))
        noshows = sum(1 for r in reservations if r.status == "noshow")
        cancelled = sum(1 for r in reservations if r.status == "cancelled")
        total_covers = sum(r.party_size for r in reservations if r.status != "cancelled")

        # Source breakdown
        sources = {}
        for r in reservations:
            sources[r.source] = sources.get(r.source, 0) + 1

        # Avg party size
        valid = [r for r in reservations if r.status not in ("cancelled",)]
        avg_party = round(sum(r.party_size for r in valid) / len(valid), 1) if valid else 0

        # Peak hours
        hours = {}
        for r in reservations:
            if r.status != "cancelled":
                h = r.time.strftime("%H:00")
                hours[h] = hours.get(h, 0) + 1

        peak_hour = max(hours, key=hours.get) if hours else "–"

        return {
            "period": f"{from_date.isoformat()} bis {to_date.isoformat()}",
            "total_reservations": total,
            "confirmed": confirmed,
            "noshows": noshows,
            "noshow_rate_pct": round((noshows / total * 100) if total else 0, 1),
            "cancelled": cancelled,
            "total_covers": total_covers,
            "avg_party_size": avg_party,
            "by_source": sources,
            "peak_hour": peak_hour,
            "busiest_hours": dict(sorted(hours.items(), key=lambda x: -x[1])[:5]),
        }

    # ─── REMINDER & NO-SHOW ────────────────────────

    def get_reservations_needing_reminder(self, hours_before: int = 4) -> list:
        """Findet Reservierungen die eine Erinnerung brauchen."""
        now = datetime.now(timezone.utc)
        target = now + timedelta(hours=hours_before)

        return (
            ReservationExtended.query
            .filter_by(tenant_id=self.tenant_id, status="confirmed")
            .filter(ReservationExtended.reminder_sent_at.is_(None))
            .filter(ReservationExtended.date == target.date())
            .all()
        )

    def auto_mark_noshows(self, grace_minutes: int = 30) -> list:
        """Markiert überfällige Reservierungen als No-Show."""
        now = datetime.now(timezone.utc)
        cutoff_time = (now - timedelta(minutes=grace_minutes)).time()

        overdue = (
            ReservationExtended.query
            .filter_by(tenant_id=self.tenant_id, status="confirmed", date=date.today())
            .filter(ReservationExtended.time <= cutoff_time)
            .all()
        )

        marked = []
        for r in overdue:
            r.status = "noshow"
            r.noshow_marked_at = now
            marked.append(r.id)

        if marked:
            db.session.commit()
            logger.info(f"Auto No-Show: {len(marked)} Reservierungen markiert")

        return marked

    # ─── PRIVATE HELPERS ────────────────────────────

    def _is_closed(self, target_date: date) -> bool:
        """Prüft ob das Restaurant an diesem Tag geschlossen ist."""
        # Spezifisches Datum
        specific = ClosedDay.query.filter_by(
            tenant_id=self.tenant_id, date=target_date
        ).first()
        if specific:
            return True

        # Wiederkehrender Wochentag
        recurring = ClosedDay.query.filter_by(
            tenant_id=self.tenant_id, recurring_weekday=target_date.weekday()
        ).first()
        return bool(recurring)

    def _find_service_period(self, target_date: date, target_time: time):
        """Findet die passende Service-Periode."""
        weekday = target_date.weekday()
        periods = ServicePeriod.query.filter_by(
            tenant_id=self.tenant_id, day_of_week=weekday, active=True
        ).all()

        for p in periods:
            if p.start_time <= target_time <= p.end_time:
                return p
        return None

    def _generate_time_slots(self, period: ServicePeriod) -> list:
        """Generiert alle Zeitslots für eine Service-Periode."""
        slots = []
        current = datetime.combine(date.today(), period.start_time)
        last = datetime.combine(date.today(), period.last_seating or period.end_time)

        while current <= last:
            slots.append(current.time())
            current += timedelta(minutes=period.slot_interval_min)

        return slots

    def _find_available_tables(self, target_date: date, target_time: time,
                               party_size: int, slot_duration: int) -> list:
        """Findet verfügbare Tische für eine bestimmte Zeit und Gruppengröße."""
        # Alle aktiven Tische die groß genug sind
        tables = (
            RestaurantTable.query
            .filter_by(tenant_id=self.tenant_id, active=True)
            .filter(RestaurantTable.max_seats >= party_size)
            .filter(RestaurantTable.min_seats <= party_size)
            .order_by(RestaurantTable.priority, RestaurantTable.max_seats)
            .all()
        )

        # Zeitfenster berechnen
        start_dt = datetime.combine(target_date, target_time)
        end_dt = start_dt + timedelta(minutes=slot_duration)

        available = []
        for table in tables:
            # Prüfe ob der Tisch in diesem Zeitfenster frei ist
            conflicting = (
                ReservationExtended.query
                .filter_by(tenant_id=self.tenant_id, date=target_date, table_id=table.id)
                .filter(ReservationExtended.status.in_(["confirmed", "seated"]))
                .all()
            )

            is_free = True
            for res in conflicting:
                res_start = datetime.combine(target_date, res.time)
                res_end = datetime.combine(target_date, res.end_time) if res.end_time else res_start + timedelta(minutes=slot_duration)

                # Überlappung prüfen
                if start_dt < res_end and end_dt > res_start:
                    is_free = False
                    break

            if is_free:
                available.append(table)

        return available

    def _find_alternative_slots(self, target_date: date, party_size: int,
                                preferred_time: time) -> list:
        """Findet alternative Zeitslots wenn der gewünschte ausgebucht ist."""
        all_slots = self.get_available_slots(target_date, party_size)

        # Nach Nähe zur gewünschten Zeit sortieren
        pref_minutes = preferred_time.hour * 60 + preferred_time.minute
        for slot in all_slots:
            h, m = map(int, slot["time"].split(":"))
            slot["_diff"] = abs((h * 60 + m) - pref_minutes)

        all_slots.sort(key=lambda s: s["_diff"])

        return [{"time": s["time"], "period": s["period"]} for s in all_slots[:5]]


# ─── SETUP HELPER ──────────────────────────────────────

def setup_restaurant_defaults(tenant_id: str, config: dict = None):
    """
    Richtet ein Restaurant mit Standard-Konfiguration ein.
    Kann vom Betreiber über das Dashboard angepasst werden.
    """
    config = config or {}

    # Default-Tische erstellen
    tables = config.get("tables", [
        {"name": "Tisch 1", "zone": "innen", "min": 2, "max": 2, "priority": 1},
        {"name": "Tisch 2", "zone": "innen", "min": 2, "max": 2, "priority": 1},
        {"name": "Tisch 3", "zone": "innen", "min": 2, "max": 4, "priority": 3},
        {"name": "Tisch 4", "zone": "innen", "min": 2, "max": 4, "priority": 3},
        {"name": "Tisch 5", "zone": "innen", "min": 4, "max": 6, "priority": 5},
        {"name": "Tisch 6", "zone": "innen", "min": 4, "max": 6, "priority": 5},
        {"name": "Tisch 7", "zone": "stube", "min": 6, "max": 8, "priority": 7},
        {"name": "Tisch 8", "zone": "stube", "min": 6, "max": 10, "priority": 8},
        {"name": "Terrasse 1", "zone": "terrasse", "min": 2, "max": 4, "priority": 2},
        {"name": "Terrasse 2", "zone": "terrasse", "min": 2, "max": 4, "priority": 2},
        {"name": "Terrasse 3", "zone": "terrasse", "min": 4, "max": 6, "priority": 4},
    ])

    for t in tables:
        table = RestaurantTable(
            tenant_id=tenant_id,
            name=t["name"],
            zone=t["zone"],
            min_seats=t["min"],
            max_seats=t["max"],
            priority=t.get("priority", 5),
        )
        db.session.add(table)

    # Standard Service-Perioden (Mo-Sa, Mittag + Abend)
    closed_day = config.get("closed_day", 0)  # Default: Montag Ruhetag

    for weekday in range(7):
        if weekday == closed_day:
            closed = ClosedDay(
                tenant_id=tenant_id,
                date=date.today(),  # Platzhalter
                recurring_weekday=weekday,
                reason="Ruhetag",
            )
            db.session.add(closed)
            continue

        # Mittagessen
        lunch = ServicePeriod(
            tenant_id=tenant_id,
            name="Mittagessen",
            day_of_week=weekday,
            start_time=time(11, 30),
            end_time=time(14, 0),
            last_seating=time(13, 30),
            slot_duration_min=90,
            slot_interval_min=30,
        )
        db.session.add(lunch)

        # Abendessen
        dinner = ServicePeriod(
            tenant_id=tenant_id,
            name="Abendessen",
            day_of_week=weekday,
            start_time=time(18, 0),
            end_time=time(22, 0),
            last_seating=time(21, 0),
            slot_duration_min=config.get("dinner_duration", 90),
            slot_interval_min=30,
        )
        db.session.add(dinner)

    db.session.commit()
    logger.info(f"Restaurant-Defaults eingerichtet für Tenant {tenant_id}")
