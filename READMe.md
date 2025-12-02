# Fitness Club Management System
**Using Python, SQLAlchemy ORM, Flask UI, and PostgreSQL**

This project implements a complete **Fitness Club Management System** with three user roles:

- **Member**
- **Trainer**
- **Administrative Staff**

It uses **SQLAlchemy ORM** for all database interactions, **PostgreSQL** as the relational database, and **Flask** for a lightweight web-based UI to demo the operations.

The system implements **all 12 core operations**, plus a required **View**, **Trigger**, and **Index**.

---

# Project Structure

```
comp3005-fitness-orm/
│
├── .env.example           # Template copied to .env for local creds
├── app/                   # Flask web UI + service layer helpers
│   ├── web_app.py         # Main Flask entry point
│   ├── init_db.py         # Creates tables + trigger/view/index
│   ├── demo_data.py       # Admin UI data reset helpers
│   ├── member_service.py
│   ├── trainer_service.py
│   ├── admin_service.py
│   └── templates/         # HTML (Bootstrap) templates
│
├── models/                # SQLAlchemy ORM models
│   ├── base.py            # engine/session setup + load_dotenv
│   ├── member.py
│   ├── scheduling.py
│   ├── equipment.py
│   ├── notification.py
│   └── payment.py
│
├── scripts/               # Stand-alone utility runners
│   ├── seed_demo_data.py
│   └── seed_admin_data.py
│
├── tests/                 # Full pytest test suite
│   ├── test_member_service.py
│   ├── test_trainer_service.py
│   └── test_admin_service.py
│
├── er_model/              # ER diagrams, PlantUML exports, screenshots
├── ER to Relational Mapping/ # Assignment write-up artifacts
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

# Installation & Setup

## Prerequisites

- **Python 3.11+** with `pip` and the built-in `venv` module available. macOS/Linux can check with `python3 --version`; Windows PowerShell can use `py -3 --version`.
- **PostgreSQL 14+** running locally. Ensure the server is started and `psql --version` returns successfully. (macOS users can `brew install postgresql@15`; Windows users can install from [postgresql.org](https://www.postgresql.org/download/).)
- **Build helpers**: keep `pip`, `setuptools`, and `wheel` current so `psycopg2-binary` installs cleanly:

  ```bash
  python3 -m pip install --upgrade pip setuptools wheel
  ```

  ```powershell
  py -m pip install --upgrade pip setuptools wheel
  ```

- **Git** for cloning and `virtualenv`/`venv` for isolation.

Once the prerequisites are in place, continue below.

## 1. Clone the repository

```bash
git clone <your_repo_url>
cd comp3005-fitness-orm
```

## 2. Create a virtual environment

**macOS / Linux (zsh or bash)**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

Keep the virtual environment active and install the Python packages for your platform.

**macOS / Linux**

```bash
python3 -m pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
py -m pip install -r requirements.txt
```


## 4. Configure your database connection

Copy the template and set your own credentials (the repo ships with **`.env.example`** so clones don't immediately fail):

```bash
cp .env.example .env
```

Then edit `.env` with the DB user/password/host for your machine. Leave the original template untouched so teammates can clone without editing tracked files. An example configuration after copying might look like:

```
DATABASE_URL=postgresql+psycopg2://bob:@localhost:5432/fitness_club
DB_USER=bob
DB_PASSWORD=
DB_NAME=fitness_club
DB_HOST=127.0.0.1
DB_PORT=5432
```

---

# Initialize the Database

Run the init module once the database server is online and `.env` contains your credentials:

```bash
$ python -m app.init_db
Database tables + view + trigger + index created.
```

The command uses SQLAlchemy metadata plus raw SQL migrations in `app/init_db.py` to create tables, ensure new columns (pricing, trainer defaults, etc.), and install the trigger, view, and index listed below.

## Trigger, View, and Index details

- **Trigger: `trg_update_member_last_metric`** — defined in `app/init_db.py` lines 83-115. Whenever a new `health_metric` row is inserted, the trigger runs `update_last_metric()` to stamp the associated `member.last_metric_at`. This feeds dashboard summaries and keeps the `member` table denormalized for quick lookups.
- **View: `member_latest_metric_view`** — also in `app/init_db.py` (lines 120-142). It selects each member with their most recent metric by using a lateral join. The web UI uses this for quick “latest vitals” cards without issuing multiple queries.
- **Index: `idx_health_metric_member_id`** — built in `app/init_db.py` (lines 148-153) on `health_metric(member_id)` so history queries in both the API and reporting pages stay fast as data grows.

---

# Seed Demo Data

You have two options for loading realistic fixtures:

1. **Command-line seeder** (`scripts/seed_demo_data.py`)  
   This script is safe to run multiple times; it upserts a pair of rooms, two trainers, two default members, upcoming classes, and private sessions with linked payments/billing rows so you can exercise the CLI/services layer without touching the UI. Typical execution:

   ```bash
   python scripts/seed_demo_data.py
   ```

   ```
   Demo data ready:
     Rooms: ['Main Room', 'Studio B']
     Trainers: Tina, Riley
     Members created for testing Alex/Jamie
   ```

2. **Admin UI demo reset** (`/admin/demo-data`)  
   Inside the Flask app, admins can open **Admin → Demo Data** to call the helpers in `app/demo_data.py`. The “Seed Demo Data” button drops/recreates every table, loads five members with recent metrics, three trainers, trainer-specific rooms, availability windows, overlapping sessions (to test conflict detection), payments, billing items, and several classes. Use this when you want the full UI populated instantly.

---

# Run the Web UI

```bash
$ python -m app.web_app
 * Serving Flask app 'app.web_app'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000/
Press CTRL+C to quit
```

Then open:

```
http://127.0.0.1:5000/
```

Once you authenticate with one of the seeded accounts (the hard-coded admin login is `admin1` / `admin123` per `app/web_app.py`), you can drive:

### UI demonstrates all 12 operations:

### ✔ Member
- Create/update profile  
- Log/view metrics  
- Book/reschedule PT sessions  
- Register for classes  

### ✔ Trainer
- Set availability  
- View schedule  
- Lookup member info  
- Create/update classes  

### ✔ Admin
- Manage rooms  
- Process payments  
- Resolve conflicts  
- Manage equipment  

All UI interactions use **pure ORM**, no raw SQL.

---

# Running Tests

```bash
pytest
```

Confirms correctness of:

- Member operations  
- Trainer operations  
- Admin operations  
- Conflict detection  
- Capacity enforcement  
- Eager loading  
- ORM model mapping  

---

# Viewing the ER MODEL DIAGRAM:

NOTE: The Final Version of the ER DIAGRAM are marked as FINAL VERSION

Step 1:

Take GitHub code referenced in ER MODEL folder and paste it into Gleek.io

```https://app.gleek.io/diagrams/3iJO4oZHTkWafVaHdnxr5A```

Step 2: 

View Image
