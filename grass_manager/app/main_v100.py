from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import Cookie, HTTPException

import main as core

app = core.app
APP_VERSION = "1.0.0"


def require_admin(token: str | None) -> dict[str, Any]:
    user = core.require_user(token)
    if user["global_role"] != "admin":
        raise HTTPException(403, "Accesso amministratore richiesto")
    return user


def remove_route(path: str, methods: set[str]) -> None:
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and methods.intersection(getattr(route, "methods", set())))
    ]


def init_v100() -> None:
    with core.db() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS subscriptions;
            DROP TABLE IF EXISTS user_subscriptions;
            DROP TABLE IF EXISTS payment_transactions;
            DROP TABLE IF EXISTS subscription_events;
            DROP TABLE IF EXISTS subscription_plans;

            CREATE TABLE IF NOT EXISTS plans(
              code TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              monthly_price REAL NOT NULL,
              features TEXT NOT NULL DEFAULT '{}',
              active INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_billing(
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              plan_code TEXT REFERENCES plans(code),
              subscription_type TEXT NOT NULL DEFAULT 'none',
              subscription_status TEXT NOT NULL DEFAULT 'not_required',
              starts_at TEXT,
              expires_at TEXT,
              lifetime INTEGER NOT NULL DEFAULT 0,
              auto_disable_on_expiry INTEGER NOT NULL DEFAULT 1,
              notes TEXT,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_history(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              amount REAL NOT NULL,
              currency TEXT NOT NULL DEFAULT 'EUR',
              payment_status TEXT NOT NULL DEFAULT 'paid',
              payment_method TEXT NOT NULL DEFAULT 'manual',
              reference TEXT,
              paid_at TEXT,
              period_start TEXT,
              period_end TEXT,
              notes TEXT,
              created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO plans(code,name,monthly_price,features,active,sort_order) VALUES('base','Base',3.99,?,1,10)",
            (json.dumps({"manual_start": True, "scheduling": True, "history_days": 30}),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO plans(code,name,monthly_price,features,active,sort_order) VALUES('premium','Premium',6.99,?,1,20)",
            (json.dumps({"manual_start": True, "scheduling": True, "history_days": 365, "weather_automation": True, "advanced_alerts": True}),),
        )
        conn.commit()


init_v100()

for path, methods in [
    ("/api/dashboard", {"GET"}),
    ("/api/plants", {"POST"}),
    ("/api/plants/{plant_id}", {"PUT"}),
    ("/api/plants/{plant_id}/details", {"GET"}),
    ("/api/users", {"GET"}),
]:
    remove_route(path, methods)


def billing_state(row: dict[str, Any] | None) -> str:
    if not row:
        return "not_configured"
    if row.get("lifetime"):
        return "lifetime"
    if row.get("subscription_type") == "none":
        return "not_required"
    if row.get("subscription_status") in {"suspended", "cancelled"}:
        return row["subscription_status"]
    expires = row.get("expires_at")
    if expires:
        try:
            if datetime.fromisoformat(expires) < datetime.now():
                return "expired"
        except ValueError:
            return "expired"
    return row.get("subscription_status") or "active"


def owner_subscription_valid(plant_id: int) -> bool:
    with core.db() as conn:
        row = conn.execute(
            """SELECT ub.* FROM user_plants up
               JOIN users u ON u.id=up.user_id
               LEFT JOIN user_billing ub ON ub.user_id=u.id
               WHERE up.plant_id=? AND up.role='owner' AND u.active=1
               ORDER BY u.id LIMIT 1""",
            (plant_id,),
        ).fetchone()
    if not row:
        return True
    return billing_state(dict(row)) in {"active", "trial", "lifetime", "not_required"}


original_access = core.plant_access
original_run_program = core.run_program


def guarded_access(user: dict[str, Any], plant_id: int, permission: str = "view") -> dict[str, Any]:
    access = original_access(user, plant_id, permission)
    if user["global_role"] != "admin" and permission in {"start", "edit_programs"} and not owner_subscription_valid(plant_id):
        raise HTTPException(402, "Abbonamento del proprietario scaduto")
    return access


async def guarded_run(program_id: int, source: str, actor_user_id: int | None) -> None:
    with core.db() as conn:
        row = conn.execute("SELECT plant_id FROM programs WHERE id=?", (program_id,)).fetchone()
    if row and not owner_subscription_valid(int(row["plant_id"])):
        core.create_alert(int(row["plant_id"]), "warning", "subscription_expired", "Abbonamento scaduto", "Programmazione sospesa perché l'abbonamento del proprietario è scaduto.")
        return
    await original_run_program(program_id, source, actor_user_id)


core.plant_access = guarded_access
core.run_program = guarded_run


@app.get("/api/dashboard")
async def dashboard(irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    ids = core.accessible_plant_ids(user)
    where = "" if ids is None else f" WHERE p.id IN ({','.join('?' * len(ids))})" if ids else " WHERE 1=0"
    params = [] if ids is None else ids
    with core.db() as conn:
        plants = [dict(r) for r in conn.execute(f"SELECT p.* FROM plants p{where} ORDER BY p.name", params)]
        for plant in plants:
            plant["open_alerts"] = conn.execute("SELECT COUNT(*) c FROM alerts WHERE plant_id=? AND status='open'", (plant["id"],)).fetchone()["c"]
            plant["offline_entities"] = conn.execute("SELECT COUNT(*) c FROM plant_entities WHERE plant_id=? AND required=1 AND (last_seen_at IS NULL OR last_state IN ('unavailable','unknown'))", (plant["id"],)).fetchone()["c"]
            plant["next_runs"] = [{"name": r["name"], "weekdays": json.loads(r["weekdays"]), "start_times": json.loads(r["start_times"])} for r in conn.execute("SELECT name,weekdays,start_times FROM programs WHERE plant_id=? AND enabled=1", (plant["id"],))]
    return {"plants": plants, "runtime": core.runtime.state, "totals": {"plants": len(plants), "alerts": sum(p["open_alerts"] for p in plants), "offline": sum(p["offline_entities"] for p in plants)}}


@app.post("/api/plants")
async def create_plant(payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    with core.db() as conn:
        cur = conn.execute("INSERT INTO plants(name,customer_name,address,timezone,enabled,notes,created_at) VALUES(?,?,?,?,?,?,?)", (payload["name"], payload.get("customer_name"), payload.get("address"), payload.get("timezone", "Europe/Rome"), 1, payload.get("notes"), core.iso()))
        conn.commit()
    core.audit(admin["id"], "create_plant", cur.lastrowid, payload)
    return {"id": cur.lastrowid}


@app.put("/api/plants/{plant_id}")
async def update_plant(plant_id: int, payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    with core.db() as conn:
        conn.execute("UPDATE plants SET name=?,customer_name=?,address=?,timezone=?,enabled=?,notes=? WHERE id=?", (payload["name"], payload.get("customer_name"), payload.get("address"), payload.get("timezone", "Europe/Rome"), 1 if payload.get("enabled", True) else 0, payload.get("notes"), plant_id))
        conn.commit()
    core.audit(admin["id"], "update_plant", plant_id, payload)
    return {"ok": True}


@app.get("/api/plants/{plant_id}/details")
async def plant_details(plant_id: int, irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    access = core.plant_access(user, plant_id)
    with core.db() as conn:
        plant = core.rowdict(conn.execute("SELECT * FROM plants WHERE id=?", (plant_id,)).fetchone())
        entities = [dict(r) for r in conn.execute("SELECT * FROM plant_entities WHERE plant_id=? ORDER BY kind,label", (plant_id,))]
        zones = [dict(r) for r in conn.execute("SELECT * FROM zones WHERE plant_id=? ORDER BY name", (plant_id,))]
        programs = []
        for row in conn.execute("SELECT * FROM programs WHERE plant_id=? ORDER BY name", (plant_id,)):
            program = dict(row)
            program["weekdays"] = json.loads(program["weekdays"])
            program["start_times"] = json.loads(program["start_times"])
            program["steps"] = [dict(x) for x in conn.execute("SELECT ps.*,z.name zone_name FROM program_steps ps JOIN zones z ON z.id=ps.zone_id WHERE ps.program_id=? ORDER BY ps.position", (program["id"],))]
            programs.append(program)
    return {"plant": plant, "entities": entities, "zones": zones, "programs": programs, "access": access}


@app.get("/api/users")
async def users(irrigation_manager_session: str | None = Cookie(None)):
    require_admin(irrigation_manager_session)
    with core.db() as conn:
        result = []
        for row in conn.execute("""SELECT u.id,u.username,u.display_name,u.email,u.global_role,u.active,u.force_password_change,u.created_at,
                                  ub.plan_code,pl.name plan_name,ub.subscription_type,ub.subscription_status,ub.starts_at,ub.expires_at,ub.lifetime,ub.auto_disable_on_expiry,ub.notes billing_notes
                                  FROM users u LEFT JOIN user_billing ub ON ub.user_id=u.id LEFT JOIN plans pl ON pl.code=ub.plan_code ORDER BY u.display_name"""):
            item = dict(row)
            item["computed_subscription_status"] = billing_state(item)
            item["plants"] = [dict(p) for p in conn.execute("SELECT up.plant_id,p.name,up.role FROM user_plants up JOIN plants p ON p.id=up.plant_id WHERE up.user_id=? ORDER BY p.name", (item["id"],))]
            item["payments"] = [dict(p) for p in conn.execute("SELECT * FROM payment_history WHERE user_id=? ORDER BY COALESCE(paid_at,created_at) DESC LIMIT 50", (item["id"],))]
            result.append(item)
    return result


@app.get("/api/plans")
async def plans(irrigation_manager_session: str | None = Cookie(None)):
    core.require_user(irrigation_manager_session)
    with core.db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM plans WHERE active=1 ORDER BY sort_order,name")]
    for row in rows:
        row["features"] = json.loads(row["features"] or "{}")
    return rows


@app.put("/api/admin/plans/{code}")
async def update_plan(code: str, payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    with core.db() as conn:
        conn.execute("UPDATE plans SET name=?,monthly_price=?,features=?,active=? WHERE code=?", (payload.get("name", code.title()), float(payload.get("monthly_price", 0)), json.dumps(payload.get("features", {}), ensure_ascii=False), 1 if payload.get("active", True) else 0, code))
        conn.commit()
    core.audit(admin["id"], "update_plan", details={"code": code})
    return {"ok": True}


@app.put("/api/admin/users/{user_id}")
async def update_user(user_id: int, payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    if user_id == admin["id"] and payload.get("active") is False:
        raise HTTPException(400, "Non puoi disattivare il tuo account")
    username = str(payload.get("username", "")).strip().lower()
    display_name = str(payload.get("display_name", "")).strip()
    if not username or not display_name:
        raise HTTPException(400, "Username e nome obbligatori")
    subscription_type = str(payload.get("subscription_type", "none"))
    if subscription_type not in {"none", "fixed", "lifetime"}:
        raise HTTPException(400, "Tipo abbonamento non valido")
    lifetime = subscription_type == "lifetime"
    status = "lifetime" if lifetime else str(payload.get("subscription_status", "active" if subscription_type == "fixed" else "not_required"))
    with core.db() as conn:
        try:
            conn.execute("UPDATE users SET username=?,display_name=?,email=?,global_role=?,active=? WHERE id=?", (username, display_name, payload.get("email") or None, payload.get("global_role", "user"), 1 if payload.get("active", True) else 0, user_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Username già esistente")
        password = str(payload.get("password", ""))
        if password:
            conn.execute("UPDATE users SET password_hash=?,force_password_change=1 WHERE id=?", (core.hash_password(password), user_id))
        conn.execute("DELETE FROM user_plants WHERE user_id=?", (user_id,))
        for assignment in payload.get("assignments", []):
            conn.execute("INSERT INTO user_plants(user_id,plant_id,role,permissions) VALUES(?,?,?,?)", (user_id, int(assignment["plant_id"]), assignment.get("role", "viewer"), "{}"))
        conn.execute("""INSERT INTO user_billing(user_id,plan_code,subscription_type,subscription_status,starts_at,expires_at,lifetime,auto_disable_on_expiry,notes,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET plan_code=excluded.plan_code,subscription_type=excluded.subscription_type,
                        subscription_status=excluded.subscription_status,starts_at=excluded.starts_at,expires_at=excluded.expires_at,lifetime=excluded.lifetime,
                        auto_disable_on_expiry=excluded.auto_disable_on_expiry,notes=excluded.notes,updated_at=excluded.updated_at""",
                     (user_id, payload.get("plan_code") or None, subscription_type, status, payload.get("starts_at") or None, None if lifetime else payload.get("expires_at") or None, 1 if lifetime else 0, 1 if payload.get("auto_disable_on_expiry", True) else 0, payload.get("billing_notes"), core.iso()))
        conn.commit()
    core.audit(admin["id"], "update_user", details={"user_id": user_id})
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: int, irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    if user_id == admin["id"]:
        raise HTTPException(400, "Non puoi eliminare il tuo account")
    with core.db() as conn:
        found = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not found:
            raise HTTPException(404, "Utente non trovato")
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    core.audit(admin["id"], "delete_user", details={"user_id": user_id})
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/payments")
async def add_payment(user_id: int, payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    amount = float(payload.get("amount", 0))
    if amount < 0:
        raise HTTPException(400, "Importo non valido")
    with core.db() as conn:
        cur = conn.execute("""INSERT INTO payment_history(user_id,amount,currency,payment_status,payment_method,reference,paid_at,period_start,period_end,notes,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (user_id, amount, payload.get("currency", "EUR"), payload.get("payment_status", "paid"), payload.get("payment_method", "manual"), payload.get("reference"), payload.get("paid_at") or core.iso(), payload.get("period_start"), payload.get("period_end"), payload.get("notes"), core.iso()))
        if payload.get("period_end"):
            conn.execute("UPDATE user_billing SET subscription_status='active',starts_at=COALESCE(?,starts_at),expires_at=?,updated_at=? WHERE user_id=? AND lifetime=0", (payload.get("period_start"), payload.get("period_end"), core.iso(), user_id))
            conn.execute("UPDATE users SET active=1 WHERE id=?", (user_id,))
        conn.commit()
    core.audit(admin["id"], "add_payment", details={"user_id": user_id, "payment_id": cur.lastrowid})
    return {"ok": True, "id": cur.lastrowid}


async def expiry_loop() -> None:
    while True:
        try:
            now = datetime.now().isoformat(timespec="seconds")
            with core.db() as conn:
                expired = conn.execute("""SELECT u.id FROM users u JOIN user_billing ub ON ub.user_id=u.id
                                          WHERE ub.subscription_type='fixed' AND ub.lifetime=0 AND ub.auto_disable_on_expiry=1
                                          AND ub.expires_at IS NOT NULL AND ub.expires_at<? AND ub.subscription_status<>'expired'""", (now,)).fetchall()
                for row in expired:
                    conn.execute("UPDATE user_billing SET subscription_status='expired',updated_at=? WHERE user_id=?", (core.iso(), row["id"]))
                    conn.execute("UPDATE users SET active=0 WHERE id=?", (row["id"],))
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
                conn.commit()
        except Exception:
            pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup_v100() -> None:
    asyncio.create_task(expiry_loop())


@app.get("/api/version-v100")
async def version_v100():
    return {"version": APP_VERSION, "billing_model": "user_only", "database_generation": "clean"}
