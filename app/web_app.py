from __future__ import annotations

from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import select, text, and_, or_
from sqlalchemy.orm import joinedload, selectinload

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort,
    jsonify,
)

from models.base import get_session, Base, engine
from models.member import Member
from models.scheduling import (
    Trainer,
    ClassSchedule,
    Room,
    TrainerAvailability,
    PrivateSession,
    ClassRegistration,
)
from models.equipment import Equipment, EquipmentIssue
from models.payment import Payment, BillingItem
from models.notification import Notification

from app.member_service import (
    create_member,
    log_health_metric,
    get_member_dashboard,
    book_private_session,
    list_upcoming_classes,
    register_for_class,
    update_member,
    get_health_history, 
)
from app.trainer_service import (
    get_trainer_schedule,
    set_trainer_availability,
    lookup_trainer_members,
    create_or_update_class,
    update_trainer_availability,
)
from app.admin_service import (
    admin_reassign_session_room,
    admin_reschedule_class,
    update_equipment_status,
    create_equipment,
    log_equipment_issue,
    update_equipment_issue_status,
    record_payment,
)
from app.demo_data import clear_all_data, seed_demo_data
from app.calendar_window import get_booking_now
from app.notification_service import (
    add_member_notification,
    add_trainer_notification,
    mark_notifications_read,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret"  # fine for local/demo use only

# Ensure all ORM tables exist (handles new tables like equipment_issue)
Base.metadata.create_all(bind=engine)


def ensure_schema_updates():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE room
                    ADD COLUMN IF NOT EXISTS primary_trainer_id INTEGER REFERENCES trainer(trainer_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE class_schedule
                    ADD COLUMN IF NOT EXISTS price NUMERIC(8,2) DEFAULT 50;
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE private_session
                    ADD COLUMN IF NOT EXISTS price NUMERIC(8,2) DEFAULT 75;
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE equipment
                    ADD COLUMN IF NOT EXISTS room_id INTEGER REFERENCES room(room_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE equipment
                    ADD COLUMN IF NOT EXISTS trainer_id INTEGER REFERENCES trainer(trainer_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE payment
                    ADD COLUMN IF NOT EXISTS private_session_id INTEGER REFERENCES private_session(session_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE billing_item
                    ADD COLUMN IF NOT EXISTS private_session_id INTEGER UNIQUE REFERENCES private_session(session_id);
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE billing_item
                    ADD COLUMN IF NOT EXISTS trainer_id INTEGER REFERENCES trainer(trainer_id);
                """
            )
        )


ensure_schema_updates()


@app.before_request
def _ensure_schema_on_request():
    """Keep lightweight schema patches applied even after demo resets."""
    ensure_schema_updates()

DURATION_OPTIONS = [30, 60, 90, 120]


def generate_weekly_time_slots(
    *,
    weeks: int = 4,
    start_hour: int = 9,
    end_hour: int = 17,
    step_minutes: int = 30,
    taken_map: dict[str, list[str]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Build grouped slot options for the next month, including taken metadata."""
    if now is None:
        now = datetime.utcnow()
    month_limit = now + timedelta(weeks=weeks)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    slots: list[dict[str, object]] = []
    for week_index in range(weeks):
        current_week_start = week_start + timedelta(weeks=week_index)
        week_label = f"Week of {current_week_start.strftime('%b %d')}"
        for day_offset in range(7):
            day = current_week_start + timedelta(days=day_offset)
            if day >= month_limit:
                break
            for minutes_since_midnight in range(start_hour * 60, end_hour * 60, step_minutes):
                slot_start = day + timedelta(minutes=minutes_since_midnight)
                if slot_start < now or slot_start >= month_limit:
                    continue
                key = slot_start.replace(second=0, microsecond=0).isoformat(timespec="minutes")
                taken_list = taken_map.get(key, []) if taken_map else []
                slots.append(
                    {
                        "value": key,
                        "time_label": slot_start.strftime("%I:%M %p"),
                        "day_label": slot_start.strftime("%A %b %d"),
                        "week_label": week_label,
                        "taken_by": taken_list,
                        "available": len(taken_list) == 0,
                    }
                )
    return slots


def build_pt_slot_groups(
    session_db,
    trainers: list[Trainer],
    *,
    now: datetime | None = None,
    weeks: int = 1,
    slot_minutes: int = 60,
) -> list[dict]:
    """Return available PT slots per trainer based on availability and conflicts."""
    if now is None:
        now = datetime.utcnow()
    horizon = now + timedelta(weeks=weeks)
    slot_delta = timedelta(minutes=slot_minutes)

    busy_map: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    private_sessions = session_db.scalars(
        select(PrivateSession)
        .where(
            PrivateSession.start_time >= now,
            PrivateSession.start_time < horizon,
        )
    ).all()
    for ps in private_sessions:
        busy_map[ps.trainer_id].append((ps.start_time, ps.end_time))

    classes = session_db.scalars(
        select(ClassSchedule)
        .where(
            ClassSchedule.start_time >= now,
            ClassSchedule.start_time < horizon,
        )
    ).all()
    for cls in classes:
        busy_map[cls.trainer_id].append((cls.start_time, cls.end_time))

    slot_groups: list[dict] = []

    for trainer in trainers:
        if not trainer.availabilities:
            continue
        slots: list[dict] = []
        for av in trainer.availabilities:
            # find first date matching availability day
            days_ahead = (av.day_of_week - now.weekday()) % 7
            first_day = (now + timedelta(days=days_ahead)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            current_day = first_day
            while current_day < horizon:
                day_start = current_day.replace(
                    hour=av.start_time.hour,
                    minute=av.start_time.minute,
                )
                day_end = current_day.replace(
                    hour=av.end_time.hour,
                    minute=av.end_time.minute,
                )
                slot_start = max(day_start, now)
                while slot_start + slot_delta <= day_end and slot_start + slot_delta <= horizon:
                    slot_end = slot_start + slot_delta
                    busy = busy_map.get(trainer.trainer_id, [])
                    if not any(not (slot_end <= b_start or slot_start >= b_end) for b_start, b_end in busy):
                        slots.append(
                            {
                                "value": f"{trainer.trainer_id}|{slot_start.isoformat(timespec='minutes')}",
                                "label": slot_start.strftime("%I:%M %p"),
                                "day_key": slot_start.strftime("%Y-%m-%d"),
                                "day_label": slot_start.strftime("%A %b %d"),
                                "start": slot_start,
                                "end": slot_end,
                            }
                        )
                    slot_start += slot_delta
                current_day += timedelta(days=7)
        if slots:
            slots.sort(key=lambda s: s["start"])
            slot_groups.append({"trainer": trainer, "slots": slots})

    return slot_groups


def build_class_slot_options(
    trainer: Trainer,
    *,
    base: datetime | None = None,
    weeks: int = 4,
    busy_windows: list[tuple[datetime, datetime]] | None = None,
) -> list[dict[str, str]]:
    """Return future class-sized slots for a trainer honoring availability + conflicts."""
    if base is None:
        base = datetime.utcnow()
    if not trainer.availabilities:
        return []

    slot_delta = timedelta(hours=1)
    week_start = (base - timedelta(days=base.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    horizon = week_start + timedelta(weeks=weeks)
    busy_windows = busy_windows or []

    slots: list[dict[str, str]] = []
    for week_index in range(weeks):
        current_week_start = week_start + timedelta(weeks=week_index)
        week_label = f"Week of {current_week_start.strftime('%b %d')}"
        for availability in trainer.availabilities:
            day = current_week_start + timedelta(days=availability.day_of_week)
            day_start = day.replace(
                hour=availability.start_time.hour,
                minute=availability.start_time.minute,
                second=0,
                microsecond=0,
            )
            day_end = day.replace(
                hour=availability.end_time.hour,
                minute=availability.end_time.minute,
                second=0,
                microsecond=0,
            )
            slot_start = day_start
            while slot_start + slot_delta <= day_end and slot_start < horizon:
                if slot_start >= base:
                    slot_end = slot_start + slot_delta
                    conflict = any(
                        not (slot_end <= start or slot_start >= end)
                        for start, end in busy_windows
                    )
                    if not conflict:
                        slots.append(
                            {
                                "value": slot_start.isoformat(timespec="minutes"),
                                "time_label": slot_start.strftime("%I:%M %p"),
                                "day_label": slot_start.strftime("%A %b %d"),
                                "week_label": week_label,
                            }
                        )
                slot_start += slot_delta

    return slots


def broadcast_class_notification(db, cls: ClassSchedule, message: str) -> None:
    if not message:
        return
    add_trainer_notification(db, cls.trainer_id, message)
    member_ids = list(
        db.scalars(
            select(ClassRegistration.member_id).where(
                ClassRegistration.class_id == cls.class_id
            )
        )
    )
    for member_id in member_ids:
        add_member_notification(db, member_id, message)


# --- Simple demo "users" for role separation (NOT production auth) ---
FAKE_USERS = {
    "admin1": {"password": "admin123", "role": "admin"},
}


def require_role(*roles: str):
    """Abort with 403 unless the current session role is in the allowed roles."""
    current = session.get("role")
    normalized_current = current.lower() if isinstance(current, str) else current
    if roles:
        allowed = {
            role.lower() if isinstance(role, str) else role
            for role in roles
        }
    else:
        allowed = {None}
    if normalized_current not in allowed:
        abort(403)


def ensure_trainer_self(trainer_id: int) -> None:
    """Only enforce trainer_id matching when the active role is trainer."""
    if session.get("role") == "trainer":
        sid = session.get("trainer_id")
        if sid and sid != trainer_id:
            abort(403)


# -------------------------------------------------
# Auth
# -------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    # -------------------- POST: authenticate --------------------
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        # 1) Admin (hard-coded)
        if username in FAKE_USERS:
            user = FAKE_USERS[username]
            if user["password"] != password:
                flash("Invalid credentials", "error")
                return redirect(url_for("login"))

            session["username"] = username
            session["role"] = user["role"]
            session["member_id"] = None
            session["trainer_id"] = None

            flash(f"Logged in as admin ({username})", "success")
            return redirect(url_for("admin_demo_data"))

        # 2) Members: username pattern "member<id>", password "member123"
        if username.startswith("member"):
            try:
                member_id = int(username[len("member"):])
            except ValueError:
                flash("Invalid member username format.", "error")
                return redirect(url_for("login"))

            if password != "member123":
                flash("Invalid member password.", "error")
                return redirect(url_for("login"))

            with get_session() as db:
                member = db.get(Member, member_id)
                if not member:
                    flash(f"Member with ID {member_id} not found.", "error")
                    return redirect(url_for("login"))

            session["username"] = username
            session["role"] = "member"
            session["member_id"] = member_id
            session["trainer_id"] = None

            flash(f"Logged in as member #{member_id}", "success")
            return redirect(url_for("member_dashboard", member_id=member_id))

        # 3) Trainers: username pattern "trainer<id>", password "trainer123"
        if username.startswith("trainer"):
            try:
                trainer_id = int(username[len("trainer"):])
            except ValueError:
                flash("Invalid trainer username format.", "error")
                return redirect(url_for("login"))

            if password != "trainer123":
                flash("Invalid trainer password.", "error")
                return redirect(url_for("login"))

            with get_session() as db:
                trainer = db.get(Trainer, trainer_id)
                if not trainer:
                    flash(f"Trainer with ID {trainer_id} not found.", "error")
                    return redirect(url_for("login"))

            session["username"] = username
            session["role"] = "trainer"
            session["member_id"] = None
            session["trainer_id"] = trainer_id

            flash(f"Logged in as trainer #{trainer_id}", "success")
            return redirect(url_for("trainer_schedule", trainer_id=trainer_id))

        # 4) If none matched
        flash("Unknown username. Use one of the demo accounts shown below.", "error")
        return redirect(url_for("login"))

    # -------------------- GET: show login + all demo accounts --------------------
    demo_accounts: list[dict] = []

    with get_session() as db:
        # All members → member<id> / member123
        all_members = list(db.scalars(select(Member).order_by(Member.member_id)))
        for m in all_members:
            demo_accounts.append(
                {
                    "role": "member",
                    "username": f"member{m.member_id}",
                    "password": "member123",
                    "member": m,
                    "trainer": None,
                }
            )

        # All trainers → trainer<id> / trainer123
        all_trainers = list(db.scalars(select(Trainer).order_by(Trainer.trainer_id)))
        for t in all_trainers:
            demo_accounts.append(
                {
                    "role": "trainer",
                    "username": f"trainer{t.trainer_id}",
                    "password": "trainer123",
                    "member": None,
                    "trainer": t,
                }
            )

    # Add hard-coded admin
    demo_accounts.append(
        {
            "role": "admin",
            "username": "admin1",
            "password": "admin123",
            "member": None,
            "trainer": None,
        }
    )

    return render_template("login.html", demo_accounts=demo_accounts)



@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


# -------------------------------------------------
# Home
# -------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("login"))


# -------------------------------------------------
# Member UI
# -------------------------------------------------
@app.route("/members/new", methods=["GET", "POST"])
def member_register():
    # you can leave this open as registration
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        target_weight = request.form.get("target_weight")
        notes = request.form.get("notes") or None

        with get_session() as db:
            try:
                m = create_member(
                    db,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    target_weight=target_weight,
                    notes=notes,
                )
                flash(f"Member created with ID {m.member_id}", "success")
                return redirect(url_for("member_dashboard", member_id=m.member_id))
            except Exception as e:
                flash(str(e), "error")

    return render_template("member_register.html")


@app.route("/members/<int:member_id>/dashboard")
def member_dashboard(member_id: int):
    require_role("member", "admin")
    if session.get("role") == "member":
        sid = session.get("member_id")
        if sid and sid != member_id:
            abort(403)

    billing_items: list[BillingItem] = []
    with get_session() as session_db:
        calendar_now = get_booking_now()
        dashboard = get_member_dashboard(session_db, member_id, now=calendar_now)
        upcoming_classes = list_upcoming_classes(session_db, now=calendar_now)
        trainer_stmt = (
            select(Trainer)
            .options(
                joinedload(Trainer.availabilities),
                joinedload(Trainer.private_sessions).joinedload(PrivateSession.room),
                joinedload(Trainer.classes).joinedload(ClassSchedule.room),
            )
            .order_by(Trainer.trainer_id)
        )
        trainers = list(session_db.execute(trainer_stmt).unique().scalars())
        rooms = list(session_db.scalars(select(Room).order_by(Room.room_id)))

        # full health history for the history table
        health_history = get_health_history(session_db, member_id)

        # EAGER load Trainer on TrainerAvailability so template can access av.trainer
        trainer_availabilities = list(
            session_db.scalars(
                select(TrainerAvailability)
                .options(joinedload(TrainerAvailability.trainer))
                .order_by(
                    TrainerAvailability.trainer_id,
                    TrainerAvailability.day_of_week,
                )
            )
        )
        pt_slot_groups = build_pt_slot_groups(
            session_db,
            trainers,
            now=calendar_now,
            weeks=1,
        )
        available_classes = [
            cls for cls in upcoming_classes
            if cls.capacity > len(cls.registrations)
        ]
        default_rooms = [{"id": r.room_id, "name": r.name} for r in rooms]
        trainer_rooms: dict[int, list[dict]] = {}
        for trainer in trainers:
            assigned = [
                {"id": r.room_id, "name": r.name}
                for r in rooms
                if r.primary_trainer_id == trainer.trainer_id
            ]
            trainer_rooms[trainer.trainer_id] = assigned or default_rooms

        selected_trainer_id = request.args.get("trainer_id", type=int)
        trainer_ids = [group["trainer"].trainer_id for group in pt_slot_groups]
        if selected_trainer_id not in trainer_ids:
            selected_trainer_id = trainer_ids[0] if trainer_ids else None

        busy_windows = [
            (ps.start_time, ps.end_time)
            for ps in dashboard["upcoming_private_sessions"]
        ]
        busy_windows.extend(
            (cls.start_time, cls.end_time)
            for cls in dashboard["upcoming_classes"]
        )

        current_slots = []
        if selected_trainer_id is not None:
            for group in pt_slot_groups:
                if group["trainer"].trainer_id == selected_trainer_id:
                    current_slots = group["slots"]
                    break
        day_options = []
        if current_slots:
            seen = set()
            for slot in current_slots:
                key = slot["day_key"]
                if key in seen:
                    continue
                seen.add(key)
                day_options.append({"key": key, "label": slot["day_label"]})
        selected_day_key = request.args.get("slot_day")
        if day_options:
            if not any(opt["key"] == selected_day_key for opt in day_options):
                selected_day_key = day_options[0]["key"]
            filtered_slots = [
                slot for slot in current_slots if slot["day_key"] == selected_day_key
            ]
        else:
            selected_day_key = None
            filtered_slots = []
        slot_options = []
        for slot in filtered_slots:
            conflict = any(
                not (slot["end"] <= start or slot["start"] >= end)
                for start, end in busy_windows
            )
            slot_options.append(
                {
                    "value": slot["value"],
                    "label": slot["label"],
                    "day_label": slot["day_label"],
                    "conflict": conflict,
                }
            )

        current_rooms = trainer_rooms.get(selected_trainer_id) if selected_trainer_id else default_rooms
        class_options = []
        for cls in available_classes:
            conflict = any(
                not (cls.end_time <= start or cls.start_time >= end)
                for start, end in busy_windows
            )
            class_options.append(
                {
                    "class": cls,
                    "remaining": cls.capacity - len(cls.registrations),
                    "conflict": conflict,
                }
            )

        billing_items = list(
            session_db.execute(
                select(BillingItem)
                .options(
                    joinedload(BillingItem.class_schedule).joinedload(ClassSchedule.trainer),
                    joinedload(BillingItem.private_session).joinedload(PrivateSession.trainer),
                    joinedload(BillingItem.trainer),
                )
                .where(BillingItem.member_id == member_id)
                .order_by(BillingItem.created_at.desc())
            )
            .unique()
            .scalars()
        )
        member_payments = list(
            session_db.scalars(
                select(Payment)
                .options(
                    joinedload(Payment.member),
                    joinedload(Payment.private_session).joinedload(PrivateSession.trainer),
                )
                .where(Payment.member_id == member_id)
                .order_by(Payment.paid_at.desc())
            )
        )
        member_pending_bills = [bill for bill in billing_items if bill.status != "paid"]
        member_notifications = list(
            session_db.scalars(
                select(Notification)
                .where(
                    Notification.member_id == member_id,
                    Notification.is_read.is_(False),
                )
                .order_by(Notification.created_at.desc())
            )
        )
        mark_notifications_read(session_db, member_notifications)
        session_db.commit()

    return render_template(
        "member_dashboard.html",
        member=dashboard["profile"]["member"],
        dashboard=dashboard,
        upcoming_classes=upcoming_classes,
        trainers=trainers,
        rooms=rooms,
        health_history=health_history,
        trainer_availabilities=trainer_availabilities,
        pt_slot_groups=pt_slot_groups,
        selected_trainer_id=selected_trainer_id,
        day_options=day_options,
        selected_day_key=selected_day_key,
        slot_options=slot_options,
        current_rooms=current_rooms,
        class_options=class_options,
        billing_items=billing_items,
        member_payments=member_payments,
        member_pending_bills=member_pending_bills,
        member_notifications=member_notifications,
    )



@app.route("/members/<int:member_id>/metrics/new", methods=["POST"])
def member_add_metric(member_id: int):
    require_role("member", "admin")
    sid = session.get("member_id")
    if sid and sid != member_id:
        abort(403)

    weight_raw = request.form.get("weight", "").strip()
    heart_rate_raw = request.form.get("heart_rate", "").strip()

    weight = None
    heart_rate = None

    try:
        if weight_raw:
            weight = float(weight_raw)
        if heart_rate_raw:
            # allow 69, 69.0, etc.
            heart_rate = int(float(heart_rate_raw))
    except ValueError:
        flash("Please enter valid numeric values for weight and heart rate.", "error")
        return redirect(url_for("member_dashboard", member_id=member_id))

    with get_session() as db:
        log_health_metric(
            db,
            member_id=member_id,
            weight=weight,
            heart_rate=heart_rate,
        )

    flash("Health metric logged.", "success")
    return redirect(url_for("member_dashboard", member_id=member_id))


@app.route("/members/<int:member_id>/sessions/book", methods=["POST"])
def member_book_session(member_id: int):
    require_role("member", "admin")
    sid = session.get("member_id")
    if sid and sid != member_id:
        abort(403)

    room_id = int(request.form["room_id"])
    slot_value = request.form.get("slot_value")
    if not slot_value or "|" not in slot_value:
        flash("Please select an available trainer slot.", "error")
        return redirect(url_for("member_dashboard", member_id=member_id))

    trainer_part, start_iso = slot_value.split("|", 1)
    trainer_id = int(trainer_part)
    start = datetime.fromisoformat(start_iso)
    end = start + timedelta(hours=1)

    with get_session() as db:
        try:
            book_private_session(
                db,
                member_id=member_id,
                trainer_id=trainer_id,
                room_id=room_id,
                start_time=start,
                end_time=end,
            )
            flash("Private session booked.", "success")
        except Exception as e:
            flash(f"Error booking session: {e}", "error")

    return redirect(url_for("member_dashboard", member_id=member_id))


@app.route("/members/<int:member_id>/classes/register", methods=["POST"])
def member_register_class(member_id: int):
    require_role("member", "admin")
    sid = session.get("member_id")
    if sid and sid != member_id:
        abort(403)

    class_id = int(request.form["class_id"])
    with get_session() as db:
        try:
            register_for_class(db, member_id=member_id, class_id=class_id)
            flash("Registered for class.", "success")
        except Exception as e:
            flash(f"Error registering for class: {e}", "error")

    return redirect(url_for("member_dashboard", member_id=member_id))


@app.route("/members/<int:member_id>/profile/update", methods=["POST"])
def member_update_profile(member_id: int):
    require_role("member", "admin")
    sid = session.get("member_id")
    if sid and sid != member_id:
        abort(403)

    first_name = request.form.get("first_name")
    last_name = request.form.get("last_name")
    email = request.form.get("email")
    target_weight = request.form.get("target_weight")
    notes = request.form.get("notes")

    with get_session() as db:
        try:
            update_member(
                db,
                member_id=member_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                target_weight=target_weight,
                notes=notes or None,
            )
            flash("Profile updated.", "success")
        except Exception as e:
            flash(f"Error updating profile: {e}", "error")

    return redirect(url_for("member_dashboard", member_id=member_id))


@app.route("/members")
def members_list():
    """List all members in a simple Bootstrap table."""
    require_role("admin")
    with get_session() as db:
        members = list(db.scalars(select(Member).order_by(Member.member_id)))
    return render_template("members_list.html", members=members)


# -------------------------------------------------
# Trainer UI
# -------------------------------------------------
@app.route("/trainers/<int:trainer_id>/schedule")
def trainer_schedule(trainer_id: int):
    require_role("trainer", "admin")
    ensure_trainer_self(trainer_id)

    with get_session() as db:
        schedule = get_trainer_schedule(db, trainer_id, now=get_booking_now())
        trainer_notifications = list(
            db.scalars(
                select(Notification)
                .where(
                    Notification.trainer_id == trainer_id,
                    Notification.is_read.is_(False),
                )
                .order_by(Notification.created_at.desc())
            )
        )
        mark_notifications_read(db, trainer_notifications)
        db.commit()
    return render_template(
        "trainer_schedule.html",
        schedule=schedule,
        trainer_notifications=trainer_notifications,
    )


@app.route("/trainers/<int:trainer_id>/availability", methods=["GET", "POST"])
def trainer_availability(trainer_id: int):
    # Role guard
    require_role("trainer", "admin")
    ensure_trainer_self(trainer_id)

    if request.method == "POST":
        action = request.form.get("action", "create")
        with get_session() as db:
            try:
                if action == "update":
                    availability_id = int(request.form["availability_id"])
                    start = datetime.strptime(request.form["start_time"], "%H:%M").time()
                    end = datetime.strptime(request.form["end_time"], "%H:%M").time()
                    update_trainer_availability(
                        db,
                        availability_id=availability_id,
                        start=start,
                        end=end,
                    )
                    flash("Availability updated.", "success")
                else:
                    day_of_week = int(request.form["day_of_week"])
                    start = datetime.strptime(request.form["start_time"], "%H:%M").time()
                    end = datetime.strptime(request.form["end_time"], "%H:%M").time()
                    set_trainer_availability(
                        db,
                        trainer_id=trainer_id,
                        day_of_week=day_of_week,
                        start=start,
                        end=end,
                    )
                    flash("Availability saved (no overlaps allowed).", "success")
            except Exception as e:
                flash(f"Error saving availability: {e}", "error")

        return redirect(url_for("trainer_availability", trainer_id=trainer_id))

    # GET: show existing availability rows
    with get_session() as db:
        trainer = db.get(Trainer, trainer_id)
        if not trainer:
            flash("Trainer not found.", "error")
            return redirect(url_for("index"))

        availabilities = list(
            db.scalars(
                select(TrainerAvailability)
                .where(TrainerAvailability.trainer_id == trainer_id)
                .order_by(
                    TrainerAvailability.day_of_week,
                    TrainerAvailability.start_time,
                )
            )
        )

    return render_template(
        "trainer_availability.html",
        trainer=trainer,
        availabilities=availabilities,
    )



@app.route("/trainers/<int:trainer_id>/members")
def trainer_member_lookup(trainer_id: int):
    require_role("trainer", "admin")
    ensure_trainer_self(trainer_id)

    q = request.args.get("q") or ""
    results = []
    with get_session() as db:
        trainer = db.get(Trainer, trainer_id)
        if not trainer:
            flash("Trainer not found.", "error")
            return redirect(url_for("index"))
        if q:
            results = lookup_trainer_members(db, trainer_id=trainer_id, name_query=q)
    return render_template(
        "trainer_members.html",
        trainer_id=trainer_id,
        trainer=trainer,
        query=q,
        results=results,
    )


@app.get("/api/trainers/<int:trainer_id>/members")
def trainer_member_lookup_api(trainer_id: int):
    """JSON endpoint for live member lookup suggestions."""
    require_role("trainer", "admin")
    sid = session.get("trainer_id")
    if sid and sid != trainer_id:
        abort(403)

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})

    with get_session() as db:
        payload = lookup_trainer_members(
            db,
            trainer_id=trainer_id,
            name_query=q,
        )

    results = []
    for info in payload:
        metric = info.get("latest_metric")
        results.append(
            {
                "member_id": info["member"].member_id,
                "name": f"{info['member'].first_name} {info['member'].last_name}",
                "target_weight": info.get("target_weight"),
                "notes": info.get("notes"),
                "latest_metric": {
                    "weight": getattr(metric, "weight", None),
                    "heart_rate": getattr(metric, "heart_rate", None),
                    "timestamp": metric.timestamp.isoformat() if metric else None,
                },
            }
        )
    return jsonify({"results": results})


@app.route("/trainers/<int:trainer_id>/classes", methods=["GET", "POST"])
def trainer_classes(trainer_id: int):
    """Classes are managed via the admin page."""
    require_role("admin")
    return redirect(url_for("admin_class_management"))


# -------------------------------------------------
# Admin UI
# -------------------------------------------------
@app.route("/admin/sessions/reassign", methods=["POST"])
def admin_reassign_session():
    require_role("admin")

    session_id = int(request.form["session_id"])
    new_room_id = int(request.form["new_room_id"])
    slot_value = request.form.get("slot_start")
    with get_session() as db:
        current_session = db.scalar(
            select(PrivateSession)
            .options(joinedload(PrivateSession.room))
            .where(PrivateSession.session_id == session_id)
        )
        if not current_session:
            flash("Session not found.", "error")
            return redirect(url_for("admin_room_booking"))
        old_start = current_session.start_time
        old_room_label = (
            current_session.room.name
            if current_session.room
            else f"Room {current_session.room_id}"
        )
        if slot_value:
            new_start = datetime.fromisoformat(slot_value)
        else:
            new_start = current_session.start_time
        new_end = new_start + timedelta(hours=1)
        overlap_filter = and_(
            PrivateSession.start_time < new_end,
            PrivateSession.end_time > new_start,
        )
        conflict_session = db.scalar(
            select(PrivateSession).where(
                PrivateSession.room_id == new_room_id,
                PrivateSession.session_id != session_id,
                overlap_filter,
            )
        )
        if conflict_session:
            flash("Selected room already has another private session in that window.", "error")
            return redirect(url_for("admin_room_booking"))
        conflict_class = db.scalar(
            select(ClassSchedule).where(
                ClassSchedule.room_id == new_room_id,
                ClassSchedule.start_time < new_end,
                ClassSchedule.end_time > new_start,
            )
        )
        if conflict_class:
            flash("Selected room has a class scheduled during that time.", "error")
            return redirect(url_for("admin_room_booking"))
        trainer_conflict = db.scalar(
            select(PrivateSession).where(
                PrivateSession.trainer_id == current_session.trainer_id,
                PrivateSession.session_id != session_id,
                overlap_filter,
            )
        )
        if trainer_conflict:
            flash("Trainer already has another private session during that time.", "error")
            return redirect(url_for("admin_room_booking"))
        trainer_class_conflict = db.scalar(
            select(ClassSchedule).where(
                ClassSchedule.trainer_id == current_session.trainer_id,
                ClassSchedule.start_time < new_end,
                ClassSchedule.end_time > new_start,
            )
        )
        if trainer_class_conflict:
            flash("Trainer is teaching a class during that time.", "error")
            return redirect(url_for("admin_room_booking"))
        try:
            updated = admin_reassign_session_room(
                db,
                session_id=session_id,
                new_room_id=new_room_id,
                new_start=new_start,
                new_end=new_end,
            )
            db.refresh(
                updated, attribute_names=["room", "member", "trainer", "start_time"]
            )
            new_room_label = (
                updated.room.name if updated.room else f"Room {updated.room_id}"
            )
            changes = []
            if old_start != updated.start_time:
                changes.append(
                    f"time {updated.start_time.strftime('%b %d %I:%M %p')}"
                )
            if old_room_label != new_room_label:
                changes.append(f"room {new_room_label}")
            message = (
                "Private session updated: " + ", ".join(changes)
                if changes
                else "Private session updated."
            )
            add_member_notification(db, updated.member_id, message)
            add_trainer_notification(db, updated.trainer_id, message)
            db.commit()
            flash("Session room updated.", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
    return redirect(url_for("admin_room_booking"))


@app.route("/admin/room-booking")
def admin_room_booking():
    require_role("admin")

    with get_session() as db:
        calendar_now = get_booking_now()
        upcoming_sessions = list(
            db.scalars(
                select(PrivateSession)
                .options(
                    joinedload(PrivateSession.trainer),
                    joinedload(PrivateSession.member),
                    joinedload(PrivateSession.room),
                )
                .order_by(PrivateSession.start_time)
                .limit(15)
            )
        )
        upcoming_classes = list(
            db.scalars(
                select(ClassSchedule)
                .options(
                    joinedload(ClassSchedule.trainer),
                    joinedload(ClassSchedule.room),
                )
                .order_by(ClassSchedule.start_time)
                .limit(15)
            )
        )
        rooms = list(db.scalars(select(Room).order_by(Room.room_id)))
        time_slots = generate_weekly_time_slots(weeks=1, now=calendar_now)
        if not time_slots:
            time_slots = generate_weekly_time_slots(weeks=2, now=calendar_now)

    return render_template(
        "admin_room_booking.html",
        sessions=upcoming_sessions,
        classes=upcoming_classes,
        rooms=rooms,
        time_slots=time_slots,
    )


@app.route("/admin/classes/reschedule", methods=["POST"])
def admin_reschedule_class_view():
    require_role("admin")

    class_id = int(request.form["class_id"])
    new_room_id = int(request.form["new_room_id"])
    slot_value = request.form["slot_start"]
    new_start = datetime.fromisoformat(slot_value)
    new_end = new_start + timedelta(hours=1)

    with get_session() as db:
        target_class = db.scalar(
            select(ClassSchedule)
            .options(joinedload(ClassSchedule.room))
            .where(ClassSchedule.class_id == class_id)
        )
        if not target_class:
            flash("Class not found.", "error")
            return redirect(url_for("admin_room_booking"))
        old_start = target_class.start_time
        old_room_label = (
            target_class.room.name if target_class.room else f"Room {target_class.room_id}"
        )
        try:
            updated_class = admin_reschedule_class(
                db,
                class_id=class_id,
                new_room_id=new_room_id,
                new_start=new_start,
                new_end=new_end,
            )
            db.refresh(updated_class, attribute_names=["room", "trainer"])
            new_room_label = (
                updated_class.room.name
                if updated_class.room
                else f"Room {updated_class.room_id}"
            )
            changes = []
            if old_start != updated_class.start_time:
                changes.append(
                    f"time {updated_class.start_time.strftime('%b %d %I:%M %p')}"
                )
            if old_room_label != new_room_label:
                changes.append(f"room {new_room_label}")
            if changes:
                message = f"Class '{updated_class.name}' updated: {', '.join(changes)}."
                broadcast_class_notification(db, updated_class, message)
                db.commit()
            flash("Class rescheduled.", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
    return redirect(url_for("admin_room_booking"))


@app.route("/admin/classes", methods=["GET", "POST"])
def admin_class_management():
    require_role("admin")

    with get_session() as db:
        trainer_stmt = (
            select(Trainer)
            .options(joinedload(Trainer.availabilities))
            .order_by(Trainer.trainer_id)
        )
        trainers = list(db.execute(trainer_stmt).unique().scalars())
        rooms = list(db.scalars(select(Room).order_by(Room.room_id)))
        classes = list(
            db.scalars(
                select(ClassSchedule)
                .options(
                    joinedload(ClassSchedule.trainer),
                    joinedload(ClassSchedule.room),
                    selectinload(ClassSchedule.registrations),
                )
                .order_by(ClassSchedule.start_time)
            )
        )

        room_map: dict[int, list[Room]] = {}
        for trainer in trainers:
            assigned = [r for r in rooms if r.primary_trainer_id == trainer.trainer_id]
            if assigned:
                assigned_ids = {r.room_id for r in assigned}
                shared = [r for r in rooms if r.room_id not in assigned_ids]
                room_map[trainer.trainer_id] = assigned + shared
            else:
                room_map[trainer.trainer_id] = list(rooms)

        trainer_classes_map: dict[int, list[ClassSchedule]] = defaultdict(list)
        for cls in classes:
            trainer_classes_map[cls.trainer_id].append(cls)
        for cls_list in trainer_classes_map.values():
            cls_list.sort(key=lambda c: c.start_time)

        selected_trainer_id = request.args.get("trainer_id", type=int)
        if trainers and (selected_trainer_id is None or selected_trainer_id not in room_map):
            selected_trainer_id = trainers[0].trainer_id

        edit_class_id = request.args.get("edit_class_id", type=int)
        post_edit_class_id: int | None = None
        if request.method == "POST":
            name = request.form.get("name")
            trainer_id = int(request.form["trainer_id"])
            selected_trainer_id = trainer_id
            room_id = int(request.form["room_id"])
            capacity = int(request.form["capacity"])
            slot_value = request.form["slot_start"]
            start_time = datetime.fromisoformat(slot_value)
            end_time = start_time + timedelta(hours=1)
            price = float(request.form.get("price", 0) or 0)
            class_id_raw = request.form.get("class_id")
            class_id = int(class_id_raw) if class_id_raw else None
            post_edit_class_id = class_id

            old_start = None
            old_room_label = None
            if class_id:
                existing_cls = db.scalar(
                    select(ClassSchedule)
                    .options(joinedload(ClassSchedule.room))
                    .where(ClassSchedule.class_id == class_id)
                )
                if not existing_cls:
                    flash("Class not found for update.", "error")
                    return redirect(url_for("admin_class_management"))
                old_start = existing_cls.start_time
                old_room_label = (
                    existing_cls.room.name
                    if existing_cls.room
                    else f"Room {existing_cls.room_id}"
                )

            try:
                updated_class = create_or_update_class(
                    db,
                    trainer_id=trainer_id,
                    name=name,
                    room_id=room_id,
                    start_time=start_time,
                    end_time=end_time,
                    capacity=capacity,
                    price=price,
                    class_id=class_id,
                )
                if class_id and updated_class:
                    db.refresh(updated_class, attribute_names=["room", "trainer"])
                    new_room_label = (
                        updated_class.room.name
                        if updated_class.room
                        else f"Room {updated_class.room_id}"
                    )
                    changes: list[str] = []
                    if old_start and old_start != updated_class.start_time:
                        changes.append(
                            f"time {updated_class.start_time.strftime('%b %d %I:%M %p')}"
                        )
                    if old_room_label and old_room_label != new_room_label:
                        changes.append(f"room {new_room_label}")
                    if changes:
                        message = (
                            f"Class '{updated_class.name}' updated: {', '.join(changes)}."
                        )
                        broadcast_class_notification(db, updated_class, message)
                        db.commit()
                flash("Class updated." if class_id else "Class created.", "success")
                return redirect(url_for("admin_class_management", trainer_id=trainer_id))
            except Exception as e:
                db.rollback()
                flash(f"Error creating class: {e}", "error")

        editing_target_id = post_edit_class_id or edit_class_id
        editing_class = None
        if editing_target_id:
            editing_class = next(
                (cls for cls in classes if cls.class_id == editing_target_id),
                None,
            )
            if editing_class:
                selected_trainer_id = editing_class.trainer_id

        calendar_now = get_booking_now()
        horizon = calendar_now + timedelta(weeks=1)
        busy_map: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)

        private_sessions = db.scalars(
            select(PrivateSession).where(
                PrivateSession.start_time >= calendar_now,
                PrivateSession.start_time < horizon,
            )
        ).all()
        for ps in private_sessions:
            busy_map[ps.trainer_id].append((ps.start_time, ps.end_time))

        editing_class_id = editing_class.class_id if editing_class else None
        for cls in classes:
            if cls.start_time < calendar_now or cls.start_time >= horizon:
                continue
            if editing_class_id and cls.class_id == editing_class_id:
                continue
            busy_map[cls.trainer_id].append((cls.start_time, cls.end_time))

        slot_map: dict[int, list[dict[str, str]]] = {}
        for trainer in trainers:
            slot_map[trainer.trainer_id] = build_class_slot_options(
                trainer,
                base=calendar_now,
                weeks=1,
                busy_windows=busy_map.get(trainer.trainer_id, []),
            )

        selected_rooms = list(room_map.get(selected_trainer_id, rooms))
        if editing_class and editing_class.room:
            if all(room.room_id != editing_class.room_id for room in selected_rooms):
                selected_rooms.append(editing_class.room)

        room_option_payload: list[dict[str, object]] = []
        seen_room_ids: set[int] = set()
        for room in selected_rooms:
            if room.room_id in seen_room_ids:
                continue
            seen_room_ids.add(room.room_id)
            room_option_payload.append(
                {
                    "room_id": room.room_id,
                    "name": room.name,
                    "capacity": room.capacity,
                }
            )

        time_slots = list(slot_map.get(selected_trainer_id, []))
        selected_slot_value = None
        if editing_class:
            selected_slot_value = editing_class.start_time.replace(
                minute=0, second=0, microsecond=0
            ).isoformat(timespec="minutes")
            if not any(slot["value"] == selected_slot_value for slot in time_slots):
                time_slots.insert(
                    0,
                    {
                        "value": selected_slot_value,
                        "week_label": "Current booking",
                        "day_label": editing_class.start_time.strftime("%A %b %d"),
                        "time_label": editing_class.start_time.strftime("%I:%M %p"),
                    },
                )

    return render_template(
        "admin_classes.html",
        trainers=trainers,
        room_options=room_option_payload,
        selected_trainer_id=selected_trainer_id,
        classes=classes,
        time_slots=time_slots,
        editing_class=editing_class,
        selected_slot_value=selected_slot_value,
        trainer_classes_map=trainer_classes_map,
    )


@app.route("/admin/demo-data", methods=["GET", "POST"])
def admin_demo_data():
    require_role("admin")

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "clear":
                clear_all_data()
                flash("All data cleared. Tables recreated.", "success")
            elif action == "seed":
                clear_all_data()
                seed_demo_data()
                flash("Demo data loaded.", "success")
            else:
                flash("Unknown action.", "error")
        except Exception as exc:
            flash(f"Error resetting data: {exc}", "error")
        return redirect(url_for("admin_demo_data"))

    return render_template("admin_demo_data.html")


@app.route("/admin/equipment", methods=["GET", "POST"])
def admin_equipment():
    """Admin page to view and update equipment status."""
    require_role("admin")

    with get_session() as db:
        if request.method == "POST":
            action = request.form.get("action")

            try:
                if action == "create_equipment":
                    name = request.form["name"]
                    status = request.form.get("status", "operational")
                    notes = request.form.get("notes") or None
                    room_id = request.form.get("room_id")
                    trainer_id = request.form.get("trainer_id")
                    create_equipment(
                        db,
                        name=name,
                        status=status,
                        notes=notes,
                        room_id=int(room_id) if room_id else None,
                        trainer_id=int(trainer_id) if trainer_id else None,
                    )
                    flash("Equipment created.", "success")
                elif action == "update_equipment":
                    equipment_id = int(request.form["equipment_id"])
                    new_status = request.form["status"]
                    notes = request.form.get("notes") or None
                    room_id = request.form.get("room_id")
                    trainer_id = request.form.get("trainer_id")
                    update_equipment_status(
                        db,
                        equipment_id,
                        new_status,
                        notes,
                        room_id=int(room_id) if room_id else None,
                        trainer_id=int(trainer_id) if trainer_id else None,
                    )
                    flash("Equipment status updated.", "success")
                elif action == "log_issue":
                    equipment_id = request.form.get("issue_equipment_id") or None
                    room_id = request.form.get("issue_room_id") or None
                    description = request.form.get("description") or ""
                    status = request.form.get("issue_status", "open")
                    log_equipment_issue(
                        db,
                        equipment_id=int(equipment_id) if equipment_id else None,
                        room_id=int(room_id) if room_id else None,
                        description=description,
                        status=status,
                    )
                    flash("Issue logged.", "success")
                elif action == "update_issue":
                    issue_id = int(request.form["issue_id"])
                    new_status = request.form["new_issue_status"]
                    resolved = new_status.lower() in {"resolved", "closed"}
                    update_equipment_issue_status(
                        db,
                        issue_id=issue_id,
                        new_status=new_status,
                        resolved=resolved,
                    )
                    flash("Issue updated.", "success")
                else:
                    flash("Unknown action.", "error")
                return redirect(url_for("admin_equipment"))
            except Exception as e:
                db.rollback()
                flash(f"Error updating equipment: {e}", "error")
                return redirect(url_for("admin_equipment"))

        equipment = list(
            db.scalars(
                select(Equipment)
                .options(
                    joinedload(Equipment.room),
                    joinedload(Equipment.trainer),
                )
                .order_by(Equipment.equipment_id)
            )
        )
        equipment_lookup = {
            e.equipment_id: {"room_id": e.room_id, "trainer_id": e.trainer_id}
            for e in equipment
        }
        issue_stmt = (
            select(EquipmentIssue)
            .options(
                joinedload(EquipmentIssue.equipment),
                joinedload(EquipmentIssue.room),
            )
            .order_by(EquipmentIssue.reported_at.desc())
        )
        issues = list(db.execute(issue_stmt).unique().scalars())
        rooms = list(db.scalars(select(Room).order_by(Room.room_id)))
        trainers = list(db.scalars(select(Trainer).order_by(Trainer.first_name)))

    return render_template(
        "admin_equipment.html",
        equipment=equipment,
        issues=issues,
        rooms=rooms,
        trainers=trainers,
        equipment_lookup=equipment_lookup,
    )


@app.route("/admin/payments", methods=["GET", "POST"])
def admin_payments():
    """Admin page to record payments and view recent history."""
    require_role("admin")

    with get_session() as db:
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "create_bill":
                    member_id = int(request.form["member_id"])
                    amount = float(request.form["amount"])
                    description = request.form.get("description") or ""
                    trainer_id_raw = request.form.get("trainer_id")
                    trainer_id = int(trainer_id_raw) if trainer_id_raw else None
                    if trainer_id and not db.get(Trainer, trainer_id):
                        raise ValueError("Trainer not found")
                    bill = BillingItem(
                        member_id=member_id,
                        class_id=None,
                        private_session_id=None,
                        amount=amount,
                        description=description or "Manual bill",
                        status="pending",
                        trainer_id=trainer_id,
                    )
                    db.add(bill)
                    db.commit()
                    flash("Billing item created.", "success")
                elif action == "mark_paid":
                    billing_id = int(request.form["billing_id"])
                    bill = db.get(BillingItem, billing_id)
                    if not bill or bill.status == "paid":
                        raise ValueError("Billing item not found or already paid")
                    record_payment(
                        db,
                        member_id=bill.member_id,
                        amount=float(bill.amount),
                        description=bill.description or "Class payment",
                        private_session_id=bill.private_session_id,
                    )
                    bill.status = "paid"
                    bill.paid_at = datetime.utcnow()
                    db.commit()
                    flash("Billing item marked as paid.", "success")
                elif action == "cancel_bill":
                    billing_id = int(request.form["billing_id"])
                    bill = db.get(BillingItem, billing_id)
                    if not bill:
                        raise ValueError("Billing item not found")
                    bill.status = "cancelled"
                    db.commit()
                    flash("Billing item cancelled.", "success")
                else:
                    flash("Unknown action.", "error")
                return redirect(url_for("admin_payments"))
            except Exception as e:
                db.rollback()
                flash(f"Error processing billing: {e}", "error")
                return redirect(url_for("admin_payments"))

        pending_bills = list(
            db.execute(
                select(BillingItem)
                .options(
                    joinedload(BillingItem.member),
                    joinedload(BillingItem.class_schedule)
                    .joinedload(ClassSchedule.trainer),
                    joinedload(BillingItem.trainer),
                )
                .where(BillingItem.status != "paid")
                .order_by(BillingItem.created_at)
            ).unique().scalars()
        )
        payments = list(
            db.execute(
                select(Payment)
                .options(joinedload(Payment.member))
                .order_by(Payment.paid_at.desc())
                .limit(20)
            ).unique().scalars()
        )
        members = list(db.scalars(select(Member).order_by(Member.first_name)))
        trainers = list(db.scalars(select(Trainer).order_by(Trainer.first_name)))

    return render_template(
        "admin_payments.html",
        pending_bills=pending_bills,
        payments=payments,
        members=members,
        trainers=trainers,
    )


# -------------------------------------------------
# Common lists for dropdowns / home
# -------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
