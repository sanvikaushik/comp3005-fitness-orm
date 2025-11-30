# COMP3005 Final Project – Fitness Club Management System
**Using Python, SQLAlchemy ORM, Flask UI, and PostgreSQL**

This project implements a complete **Fitness Club Management System** with three user roles:

- **Member**
- **Trainer**
- **Administrative Staff**

It uses **SQLAlchemy ORM** for all database interactions, **PostgreSQL** as the relational database, and **Flask** for a lightweight web-based UI to demo the operations.

The system implements **all 12 core operations**, plus a required **View**, **Trigger**, and **Index**, and satisfies the course ORM rubric.

---

# Project Structure

```
comp3005-fitness-orm/
│
├── app/                   # Flask web UI + service layer
│   ├── web_app.py
│   ├── member_service.py
│   ├── trainer_service.py
│   ├── admin_service.py
│   └── templates/         # HTML (Bootstrap) templates
│
├── models/               # SQLAlchemy ORM models
│   ├── base.py
│   ├── member.py
│   └── scheduling.py
│
├── scripts/
│   ├── seed_demo_data.py  # Populate trainer/room/class via ORM
│
├── tests/                # Full pytest test suite
│
├── docs/
│   └── ERD.pdf
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

# Installation & Setup

## 1. Clone the repository

```bash
git clone <your_repo_url>
cd comp3005-fitness-orm
```

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure your database connection

Copy the template and set your own credentials (the repo ships with **`.env.example`** so clones don't immediately fail):

```bash
cp .env.example .env
```

Then edit `.env` with the DB user/password/host for your machine. Leave the original template untouched so teammates can clone without editing tracked files.

---

# Initialize the Database

```bash
python -m app.init_db
```

Creates all ORM tables + installs:

- View  
- Trigger  
- Index  

---

# Seed Demo Data

```bash
python scripts/seed_demo_data.py
```

Outputs:

```
Created Trainer ID: 1
Created Room ID: 1
Created Class ID: 1
```

---

# 🖥️ Run the Web UI

```bash
python -m app.web_app
```

Then open:

```
http://127.0.0.1:5000/
```

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

