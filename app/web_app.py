from __future__ import annotations

from datetime import datetime
from sqlalchemy import select

from flask import Flask, render_template, request, redirect, url_for, flash
from models.scheduling import Trainer, ClassSchedule, Room

from models.base import get_session
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
)
from models.scheduling import Trainer, Room, ClassSchedule

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
    weight = request.form.get("weight")
    heart_rate = request.form.get("heart_rate")

    with get_session() as session:
        log_health_metric(
            session,
            member_id=member_id,
            weight=float(weight) if weight else None,
            heart_rate=int(heart_rate) if heart_rate else None,
        )
    flash("Health metric logged.", "success")
    return redirect(url_for("member_dashboard", member_id=member_id))


@app.route("/members/<int:member_id>/sessions/book", methods=["POST"])
def member_book_session(member_id: int):
    trainer_id = int(request.form["trainer_id"])
    room_id = int(request.form["room_id"])
    # start = datetime.fromisoformat(request.form["start_time"])
    # end = datetime.fromisoformat(request.form["end_time"])

    start_time = time.fromisoformat(start_str)  # "09:00"
    end_time = time.fromisoformat(end_str)

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
def trainer_manage_classes(trainer_id: int):
    if request.method == "POST":
        class_id_raw = request.form.get("class_id")  # optional for update
        name = request.form.get("name")
        room_id_raw = request.form.get("room_id")
        start_raw = request.form.get("start_time")
        end_raw = request.form.get("end_time")
        capacity_raw = request.form.get("capacity")

        try:
            room_id = int(room_id_raw) if room_id_raw else None
            capacity = int(capacity_raw) if capacity_raw else None

            start_time = datetime.fromisoformat(start_raw) if start_raw else None
            end_time = datetime.fromisoformat(end_raw) if end_raw else None


            class_id = int(class_id_raw) if class_id_raw else None

            with get_session() as session:
                cls = create_or_update_class(
                    session=session,
                    trainer_id=trainer_id,
                    name=name,
                    room_id=room_id,
                    start_time=start_time,
                    end_time=end_time,
                    capacity=capacity,
                    class_id=class_id,  # None = create, not None = update
                )

            if class_id:
                flash(f"Class {cls.class_id} updated.", "success")
            else:
                flash(f"Class {cls.class_id} created.", "success")

        except Exception as e:
            flash(f"Error saving class: {e}", "error")

        return redirect(url_for("trainer_manage_classes", trainer_id=trainer_id))

    # GET: show trainer's classes
    with get_session() as session:
        trainer = session.get(Trainer, trainer_id)
        if not trainer:
            flash("Trainer not found.", "error")
            return redirect(url_for("index"))

        stmt = (
            select(ClassSchedule)
            .where(ClassSchedule.trainer_id == trainer_id)
            .order_by(ClassSchedule.start_time)
        )
        classes = list(session.scalars(stmt))

        # optional: all rooms for a dropdown
        rooms = list(session.scalars(select(Room).order_by(Room.room_id)))

    return render_template(
        "trainer_classes.html",
        trainer=trainer,
        classes=classes,
        rooms=rooms,
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
