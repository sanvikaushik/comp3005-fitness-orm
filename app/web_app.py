from __future__ import annotations

from datetime import datetime
from sqlalchemy import select

from flask import Flask, render_template, request, redirect, url_for, flash

from models.base import get_session
from models.member import Member
from models.scheduling import Trainer, ClassSchedule, Room
from models.equipment import Equipment
from models.payment import Payment

from app.member_service import (
    create_member,
    log_health_metric,
    get_member_dashboard,
    book_private_session,
    list_upcoming_classes,
    register_for_class,
    update_member,
)
from app.trainer_service import (
    get_trainer_schedule,
    set_trainer_availability,
    lookup_trainer_members,
    create_or_update_class,
)
from app.admin_service import (
    admin_reassign_session_room,
    admin_reschedule_class,
    update_equipment_status,
    record_payment,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret"  # fine for local/demo use only

# -------------------------------------------------
# Home
# -------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# -------------------------------------------------
# Member UI
# -------------------------------------------------
@app.route("/members/new", methods=["GET", "POST"])
def member_register():
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        target_weight = request.form.get("target_weight") or None
        notes = request.form.get("notes") or None

        with get_session() as session:
            try:
                m = create_member(
                    session,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    target_weight=float(target_weight) if target_weight else None,
                    notes=notes,
                )
                flash(f"Member created with ID {m.member_id}", "success")
                return redirect(url_for("member_dashboard", member_id=m.member_id))
            except Exception as e:
                flash(str(e), "error")

    return render_template("member_register.html")


@app.route("/members/<int:member_id>/dashboard")
def member_dashboard(member_id: int):
    with get_session() as session:
        dashboard = get_member_dashboard(session, member_id)
        upcoming_classes = list_upcoming_classes(session)
    return render_template(
        "member_dashboard.html",
        member=dashboard["profile"]["member"],
        dashboard=dashboard,
        upcoming_classes=upcoming_classes,
    )


@app.route("/members/<int:member_id>/metrics/new", methods=["POST"])
def member_add_metric(member_id: int):
    weight_raw = request.form.get("weight", "").strip()
    heart_rate_raw = request.form.get("heart_rate", "").strip()

    # Parse safely
    weight = None
    heart_rate = None

    try:
        if weight_raw:
            weight = float(weight_raw)

        if heart_rate_raw:
            # Allow "69.0", "69", even with extra spaces/tabs
            heart_rate = int(float(heart_rate_raw))
    except ValueError:
        flash("Please enter valid numeric values for weight and heart rate.", "error")
        return redirect(url_for("member_dashboard", member_id=member_id))

    with get_session() as session:
        log_health_metric(
            session,
            member_id=member_id,
            weight=weight,
            heart_rate=heart_rate,
        )

    flash("Health metric logged.", "success")
    return redirect(url_for("member_dashboard", member_id=member_id))


@app.route("/members/<int:member_id>/sessions/book", methods=["POST"])
def member_book_session(member_id: int):
    trainer_id = int(request.form["trainer_id"])
    room_id = int(request.form["room_id"])
    start = datetime.fromisoformat(request.form["start_time"])
    end = datetime.fromisoformat(request.form["end_time"])

    with get_session() as session:
        try:
            book_private_session(
                session,
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
    class_id = int(request.form["class_id"])
    with get_session() as session:
        try:
            register_for_class(session, member_id=member_id, class_id=class_id)
            flash("Registered for class.", "success")
        except Exception as e:
            flash(f"Error registering for class: {e}", "error")
    return redirect(url_for("member_dashboard", member_id=member_id))

@app.route("/members/<int:member_id>/profile/update", methods=["POST"])
def member_update_profile(member_id: int):
    first_name = request.form.get("first_name")
    last_name = request.form.get("last_name")
    email = request.form.get("email")
    target_weight = request.form.get("target_weight")
    notes = request.form.get("notes")

    with get_session() as session:
        try:
            update_member(
                session,
                member_id=member_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                target_weight=float(target_weight) if target_weight else None,
                notes=notes or None,
            )
            flash("Profile updated.", "success")
        except Exception as e:
            flash(f"Error updating profile: {e}", "error")

    return redirect(url_for("member_dashboard", member_id=member_id))

@app.route("/members")
def members_list():
    """List all members in a simple Bootstrap table."""
    with get_session() as session:
        members = list(session.scalars(select(Member).order_by(Member.member_id)))
    return render_template("members_list.html", members=members)

# -------------------------------------------------
# Trainer UI
# -------------------------------------------------
@app.route("/trainers/<int:trainer_id>/schedule")
def trainer_schedule(trainer_id: int):
    with get_session() as session:
        schedule = get_trainer_schedule(session, trainer_id)
    return render_template("trainer_schedule.html", schedule=schedule)


@app.route("/trainers/<int:trainer_id>/availability", methods=["GET", "POST"])
def trainer_availability(trainer_id: int):
    message = None
    if request.method == "POST":
        day_of_week = int(request.form["day_of_week"])
        start_str = request.form["start_time"]
        end_str = request.form["end_time"]
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()

        with get_session() as session:
            try:
                set_trainer_availability(
                    session,
                    trainer_id=trainer_id,
                    day_of_week=day_of_week,
                    start=start,
                    end=end,
                )
                flash("Availability saved.", "success")
            except Exception as e:
                flash(f"Error: {e}", "error")

    return render_template("trainer_availability.html", trainer_id=trainer_id)


@app.route("/trainers/<int:trainer_id>/members")
def trainer_member_lookup(trainer_id: int):
    q = request.args.get("q") or ""
    results = []
    if q:
        with get_session() as session:
            results = lookup_trainer_members(session, trainer_id=trainer_id, name_query=q)
    return render_template(
        "trainer_members.html",
        trainer_id=trainer_id,
        query=q,
        results=results,
    )

@app.route("/trainers/<int:trainer_id>/classes", methods=["GET", "POST"])
def trainer_classes(trainer_id: int):
    """Trainer view to create new group classes and see existing ones."""
    with get_session() as session:
        trainer = session.get(Trainer, trainer_id)
        if not trainer:
            flash(f"Trainer {trainer_id} not found.", "error")
            return redirect(url_for("index"))

        rooms = list(session.scalars(select(Room).order_by(Room.room_id)))

        if request.method == "POST":
            name = request.form.get("name")
            room_id = int(request.form["room_id"])
            capacity = int(request.form["capacity"])
            start_time = datetime.fromisoformat(request.form["start_time"])
            end_time = datetime.fromisoformat(request.form["end_time"])

            try:
                create_or_update_class(
                    session,
                    trainer_id=trainer_id,
                    name=name,
                    room_id=room_id,
                    start_time=start_time,
                    end_time=end_time,
                    capacity=capacity,
                )
                flash("Class created successfully.", "success")
                return redirect(url_for("trainer_classes", trainer_id=trainer_id))
            except Exception as e:
                flash(f"Error creating class: {e}", "error")

        classes = list(
            session.scalars(
                select(ClassSchedule)
                .where(ClassSchedule.trainer_id == trainer_id)
                .order_by(ClassSchedule.start_time)
            )
        )

    return render_template(
        "trainer_classes.html",
        trainer=trainer,
        rooms=rooms,
        classes=classes,
    )

# -------------------------------------------------
# Admin UI
# -------------------------------------------------
@app.route("/admin/sessions/reassign", methods=["POST"])
def admin_reassign_session():
    session_id = int(request.form["session_id"])
    new_room_id = int(request.form["new_room_id"])
    with get_session() as session:
        try:
            admin_reassign_session_room(
                session,
                session_id=session_id,
                new_room_id=new_room_id,
            )
            flash("Session room updated.", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
    return redirect(url_for("index"))


@app.route("/admin/classes/reschedule", methods=["POST"])
def admin_reschedule_class_view():
    class_id = int(request.form["class_id"])
    new_room_id = int(request.form["new_room_id"])
    new_start = datetime.fromisoformat(request.form["new_start"])
    new_end = datetime.fromisoformat(request.form["new_end"])

    with get_session() as session:
        try:
            admin_reschedule_class(
                session,
                class_id=class_id,
                new_room_id=new_room_id,
                new_start=new_start,
                new_end=new_end,
            )
            flash("Class rescheduled.", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
    return redirect(url_for("index"))

@app.route("/admin/equipment", methods=["GET", "POST"])
def admin_equipment():
    """Admin page to view and update equipment status."""
    with get_session() as session:
        if request.method == "POST":
            equipment_id = int(request.form["equipment_id"])
            new_status = request.form["status"]

            try:
                update_equipment_status(session, equipment_id, new_status)
                flash("Equipment status updated.", "success")
                return redirect(url_for("admin_equipment"))
            except Exception as e:
                flash(f"Error updating equipment: {e}", "error")

        equipment = list(
            session.scalars(select(Equipment).order_by(Equipment.equipment_id))
        )

    return render_template("admin_equipment.html", equipment=equipment)

@app.route("/admin/payments", methods=["GET", "POST"])
def admin_payments():
    """Admin page to record payments and view recent history."""
    with get_session() as session:
        if request.method == "POST":
            member_id = int(request.form["member_id"])
            amount = float(request.form["amount"])
            description = request.form.get("description") or None

            try:
                record_payment(session, member_id, amount, description)
                flash("Payment recorded.", "success")
                return redirect(url_for("admin_payments"))
            except Exception as e:
                # Reset the session transaction so we can keep using it
                session.rollback()
                flash(f"Error recording payment: {e}", "error")
                # optional: redirect to clear the form
                return redirect(url_for("admin_payments"))

        payments = list(
            session.scalars(
                select(Payment)
                .order_by(Payment.paid_at.desc())
                .limit(20)
            )
        )

    return render_template("admin_payments.html", payments=payments)

# Simple helper to show lists of trainers/rooms/classes on the home page
@app.context_processor
def inject_common_lists():
    with get_session() as session:
        trainers = session.query(Trainer).all()
        rooms = session.query(Room).all()
        classes = session.query(ClassSchedule).all()
    return dict(all_trainers=trainers, all_rooms=rooms, all_classes=classes)


if __name__ == "__main__":
    app.run(debug=True)
