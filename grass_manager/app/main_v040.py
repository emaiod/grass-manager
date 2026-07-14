from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Cookie, HTTPException, Request

import main as legacy

app = legacy.app
APP_VERSION = "0.4.0"
ORIGINAL_PLANT_ACCESS = legacy.plant_access
ORIGINAL_RUN_PROGRAM = legacy.run_program

ACTIVE_SUBSCRIPTION_STATES = {"trial", "active"}
PAID_ROLES = {"owner"}
FREE_ROLES = {"gardener", "maintainer", "viewer"}


def _table_columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _option(name: str, default: Any) -> Any:
    try:
        data = json.loads(Path("/data/options.json").read_text())
        return data.get(name, default)
    except Exception:
        return default


def migrate_v040() -> None:
    with legacy.db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscription_plans(
              code TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              monthly_price REAL NOT NULL,
              features TEXT NOT NULL DEFAULT '{}',
              active INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_subscriptions(
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              plan_code TEXT NOT NULL REFERENCES subscription_plans(code),
              status TEXT NOT NULL DEFAULT 'trial',
              monthly_price REAL NOT NULL,
              current_period_start TEXT,
              current_period_end TEXT,
              cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
              provider TEXT NOT NULL DEFAULT 'manual',
              provider_customer_id TEXT,
              provider_subscription_id TEXT,
              grace_until TEXT,
              suspended_at TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_transactions(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              subscription_user_id INTEGER REFERENCES user_subscriptions(user_id) ON DELETE SET NULL,
              provider TEXT NOT NULL DEFAULT 'manual',
              provider_payment_id TEXT,
              amount REAL NOT NULL,
              currency TEXT NOT NULL DEFAULT 'EUR',
              status TEXT NOT NULL,
              paid_at TEXT,
              period_start TEXT,
              period_end TEXT,
              metadata TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE(provider, provider_payment_id)
            );

            CREATE TABLE IF NOT EXISTS user_zone_assignments(
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
              can_view INTEGER NOT NULL DEFAULT 1,
              can_start INTEGER NOT NULL DEFAULT 1,
              can_program INTEGER NOT NULL DEFAULT 1,
              assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
              assigned_at TEXT NOT NULL,
              PRIMARY KEY(user_id, zone_id)
            );

            CREATE TABLE IF NOT EXISTS subscription_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              event_type TEXT NOT NULL,
              old_status TEXT,
              new_status TEXT,
              details TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            """
        )
        now = legacy.iso()
        plans = [
            (
                "base",
                "Base",
                3.99,
                json.dumps(
                    {
                        "manual_start": True,
                        "scheduling": True,
                        "history_days": 30,
                        "weather_automation": False,
                        "advanced_alerts": False,
                    }
                ),
                10,
            ),
            (
                "premium",
                "Premium",
                6.99,
                json.dumps(
                    {
                        "manual_start": True,
                        "scheduling": True,
                        "history_days": 365,
                        "weather_automation": True,
                        "advanced_alerts": True,
                    }
                ),
                20,
            ),
        ]
        for code, name, price, features, order in plans:
            conn.execute(
                """INSERT INTO subscription_plans(code,name,monthly_price,features,active,sort_order,updated_at)
                   VALUES(?,?,?,?,1,?,?)
                   ON CONFLICT(code) DO UPDATE SET
                     name=excluded.name,
                     features=excluded.features,
                     sort_order=excluded.sort_order""",
                (code, name, price, features, order, now),
            )

        # Migra gli abbonamenti impianto esistenti sul proprietario assegnato.
        if "subscriptions" in {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            owners = conn.execute(
                """SELECT up.user_id, s.plan, s.status, s.monthly_price, s.renewal_date, s.notes
                   FROM user_plants up
                   JOIN subscriptions s ON s.plant_id=up.plant_id
                   WHERE up.role='owner'
                   GROUP BY up.user_id"""
            ).fetchall()
            for owner in owners:
                plan = owner["plan"] if owner["plan"] in {"base", "premium"} else "base"
                default_price = 6.99 if plan == "premium" else 3.99
                conn.execute(
                    """INSERT OR IGNORE INTO user_subscriptions(
                         user_id,plan_code,status,monthly_price,current_period_end,provider,notes,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'manual',?,?,?)""",
                    (
                        owner["user_id"],
                        plan,
                        owner["status"] or "trial",
                        float(owner["monthly_price"] or default_price),
                        owner["renewal_date"],
                        owner["notes"],
                        now,
                        now,
                    ),
                )
        conn.commit()


def subscription_for_user(user_id: int) -> dict[str, Any] | None:
    with legacy.db() as conn:
        row = conn.execute(
            """SELECT us.*,sp.name plan_name,sp.features
               FROM user_subscriptions us
               JOIN subscription_plans sp ON sp.code=us.plan_code
               WHERE us.user_id=?""",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["features"] = json.loads(result.get("features") or "{}")
    return result


def subscription_is_valid(subscription: dict[str, Any] | None) -> bool:
    if not subscription or subscription["status"] not in ACTIVE_SUBSCRIPTION_STATES:
        return False
    end = subscription.get("current_period_end")
    grace = subscription.get("grace_until")
    limit = grace or end
    if not limit:
        return True
    try:
        return datetime.fromisoformat(limit) >= datetime.utcnow()
    except ValueError:
        return False


def plant_owner_id(plant_id: int) -> int | None:
    with legacy.db() as conn:
        row = conn.execute(
            "SELECT user_id FROM user_plants WHERE plant_id=? AND role='owner' ORDER BY user_id LIMIT 1",
            (plant_id,),
        ).fetchone()
    return int(row["user_id"]) if row else None


def plant_subscription(plant_id: int) -> dict[str, Any] | None:
    owner_id = plant_owner_id(plant_id)
    return subscription_for_user(owner_id) if owner_id else None


def ensure_plant_subscription_active(plant_id: int) -> None:
    owner_id = plant_owner_id(plant_id)
    if owner_id is None:
        return
    if not subscription_is_valid(subscription_for_user(owner_id)):
        raise HTTPException(402, "Abbonamento proprietario scaduto o sospeso")


def patched_plant_access(user: dict[str, Any], plant_id: int, permission: str = "view") -> dict[str, Any]:
    access = ORIGINAL_PLANT_ACCESS(user, plant_id, permission)
    if user["global_role"] != "admin" and permission in {"start", "edit_programs"}:
        ensure_plant_subscription_active(plant_id)
    return access


async def patched_run_program(program_id: int, source: str, actor_user_id: int | None) -> None:
    with legacy.db() as conn:
        row = conn.execute("SELECT plant_id FROM programs WHERE id=?", (program_id,)).fetchone()
    if not row:
        return
    try:
        ensure_plant_subscription_active(int(row["plant_id"]))
    except HTTPException:
        legacy.create_alert(
            int(row["plant_id"]),
            "warning",
            "subscription_expired",
            "Programmazioni sospese",
            "Il programma non è stato avviato perché l'abbonamento del proprietario è scaduto o sospeso.",
        )
        return
    await ORIGINAL_RUN_PROGRAM(program_id, source, actor_user_id)


legacy.plant_access = patched_plant_access
legacy.run_program = patched_run_program
migrate_v040()


async def subscription_enforcement_loop() -> None:
    while True:
        try:
            if _option("suspend_automation_on_expiry", True):
                with legacy.db() as conn:
                    rows = conn.execute(
                        """SELECT DISTINCT p.id plant_id
                           FROM plants p
                           JOIN user_plants up ON up.plant_id=p.id AND up.role='owner'"""
                    ).fetchall()
                    for row in rows:
                        plant_id = int(row["plant_id"])
                        valid = subscription_is_valid(plant_subscription(plant_id))
                        if not valid:
                            conn.execute("UPDATE programs SET enabled=0 WHERE plant_id=? AND enabled=1", (plant_id,))
                    conn.commit()
        except Exception:
            pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup_v040() -> None:
    asyncio.create_task(subscription_enforcement_loop())


@app.get("/api/plans")
async def plans(irrigation_manager_session: str | None = Cookie(None)):
    legacy.require_user(irrigation_manager_session)
    with legacy.db() as conn:
        rows = conn.execute(
            "SELECT code,name,monthly_price,features FROM subscription_plans WHERE active=1 ORDER BY sort_order,name"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["features"] = json.loads(item["features"] or "{}")
        result.append(item)
    return result


@app.get("/api/me/subscription")
async def my_subscription(irrigation_manager_session: str | None = Cookie(None)):
    user = legacy.require_user(irrigation_manager_session)
    subscription = subscription_for_user(int(user["id"]))
    with legacy.db() as conn:
        roles = [
            row["role"]
            for row in conn.execute("SELECT DISTINCT role FROM user_plants WHERE user_id=?", (user["id"],))
        ]
        payments = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM payment_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                (user["id"],),
            )
        ]
    pays = "owner" in roles
    return {
        "subscription": subscription,
        "valid": subscription_is_valid(subscription) if pays else True,
        "billing_required": pays,
        "free_access_roles": [role for role in roles if role in FREE_ROLES],
        "payments": payments,
    }


@app.put("/api/me/subscription")
async def change_my_plan(payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    user = legacy.require_user(irrigation_manager_session)
    plan_code = str(payload.get("plan_code", "")).strip().lower()
    with legacy.db() as conn:
        owner = conn.execute(
            "SELECT 1 FROM user_plants WHERE user_id=? AND role='owner' LIMIT 1", (user["id"],)
        ).fetchone()
        if not owner:
            raise HTTPException(403, "Solo un proprietario gestisce un abbonamento")
        plan = conn.execute(
            "SELECT code,monthly_price FROM subscription_plans WHERE code=? AND active=1", (plan_code,)
        ).fetchone()
        if not plan:
            raise HTTPException(404, "Piano non disponibile")
        now = legacy.iso()
        current = conn.execute("SELECT status FROM user_subscriptions WHERE user_id=?", (user["id"],)).fetchone()
        status = current["status"] if current else "trial"
        conn.execute(
            """INSERT INTO user_subscriptions(user_id,plan_code,status,monthly_price,provider,created_at,updated_at)
               VALUES(?,?,?,?, 'manual',?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 plan_code=excluded.plan_code,
                 monthly_price=excluded.monthly_price,
                 updated_at=excluded.updated_at""",
            (user["id"], plan["code"], status, plan["monthly_price"], now, now),
        )
        conn.execute(
            "INSERT INTO subscription_events(user_id,event_type,old_status,new_status,details,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], "plan_changed", status, status, json.dumps({"plan_code": plan_code}), now),
        )
        conn.commit()
    legacy.audit(user["id"], "change_subscription_plan", details={"plan_code": plan_code})
    return {"ok": True, "subscription": subscription_for_user(int(user["id"]))}


@app.put("/api/admin/users/{user_id}/subscription")
async def admin_update_subscription(
    user_id: int,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    admin = legacy.require_user(irrigation_manager_session)
    if admin["global_role"] != "admin":
        raise HTTPException(403)
    plan_code = str(payload.get("plan_code", "base")).lower()
    status = str(payload.get("status", "active")).lower()
    if status not in {"trial", "active", "past_due", "suspended", "cancelled", "expired"}:
        raise HTTPException(400, "Stato abbonamento non valido")
    with legacy.db() as conn:
        plan = conn.execute(
            "SELECT monthly_price FROM subscription_plans WHERE code=? AND active=1", (plan_code,)
        ).fetchone()
        if not plan:
            raise HTTPException(404, "Piano non disponibile")
        old = conn.execute("SELECT status FROM user_subscriptions WHERE user_id=?", (user_id,)).fetchone()
        now = legacy.iso()
        price = float(payload.get("monthly_price", plan["monthly_price"]))
        conn.execute(
            """INSERT INTO user_subscriptions(
                 user_id,plan_code,status,monthly_price,current_period_start,current_period_end,
                 cancel_at_period_end,provider,provider_customer_id,provider_subscription_id,
                 grace_until,suspended_at,notes,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 plan_code=excluded.plan_code,status=excluded.status,monthly_price=excluded.monthly_price,
                 current_period_start=excluded.current_period_start,current_period_end=excluded.current_period_end,
                 cancel_at_period_end=excluded.cancel_at_period_end,provider=excluded.provider,
                 provider_customer_id=excluded.provider_customer_id,
                 provider_subscription_id=excluded.provider_subscription_id,
                 grace_until=excluded.grace_until,suspended_at=excluded.suspended_at,
                 notes=excluded.notes,updated_at=excluded.updated_at""",
            (
                user_id,
                plan_code,
                status,
                price,
                payload.get("current_period_start"),
                payload.get("current_period_end"),
                1 if payload.get("cancel_at_period_end") else 0,
                payload.get("provider", "manual"),
                payload.get("provider_customer_id"),
                payload.get("provider_subscription_id"),
                payload.get("grace_until"),
                now if status == "suspended" else None,
                payload.get("notes"),
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO subscription_events(user_id,event_type,old_status,new_status,details,created_at) VALUES(?,?,?,?,?,?)",
            (
                user_id,
                "admin_update",
                old["status"] if old else None,
                status,
                json.dumps(payload, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    legacy.audit(admin["id"], "admin_update_subscription", details={"user_id": user_id, **payload})
    return {"ok": True, "subscription": subscription_for_user(user_id)}


@app.post("/api/admin/users/{user_id}/payments")
async def record_payment(
    user_id: int,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    admin = legacy.require_user(irrigation_manager_session)
    if admin["global_role"] != "admin":
        raise HTTPException(403)
    amount = float(payload.get("amount", 0))
    if amount <= 0:
        raise HTTPException(400, "Importo non valido")
    with legacy.db() as conn:
        now = legacy.iso()
        cur = conn.execute(
            """INSERT INTO payment_transactions(
                 user_id,subscription_user_id,provider,provider_payment_id,amount,currency,status,
                 paid_at,period_start,period_end,metadata,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                user_id,
                payload.get("provider", "manual"),
                payload.get("provider_payment_id"),
                amount,
                payload.get("currency", "EUR"),
                payload.get("status", "paid"),
                payload.get("paid_at", now),
                payload.get("period_start"),
                payload.get("period_end"),
                json.dumps(payload.get("metadata", {}), ensure_ascii=False),
                now,
            ),
        )
        if payload.get("status", "paid") == "paid":
            conn.execute(
                """UPDATE user_subscriptions SET status='active',current_period_start=?,current_period_end=?,
                   suspended_at=NULL,updated_at=? WHERE user_id=?""",
                (payload.get("period_start"), payload.get("period_end"), now, user_id),
            )
        conn.commit()
    legacy.audit(admin["id"], "record_payment", details={"user_id": user_id, "payment_id": cur.lastrowid})
    return {"ok": True, "id": cur.lastrowid}


@app.get("/api/plants/{plant_id}/zone-assignments")
async def zone_assignments(plant_id: int, irrigation_manager_session: str | None = Cookie(None)):
    user = legacy.require_user(irrigation_manager_session)
    if user["global_role"] != "admin":
        raise HTTPException(403)
    with legacy.db() as conn:
        rows = conn.execute(
            """SELECT uza.*,z.name zone_name,u.username,u.display_name,up.role plant_role
               FROM user_zone_assignments uza
               JOIN zones z ON z.id=uza.zone_id
               JOIN users u ON u.id=uza.user_id
               LEFT JOIN user_plants up ON up.user_id=uza.user_id AND up.plant_id=z.plant_id
               WHERE z.plant_id=? ORDER BY z.name,u.display_name""",
            (plant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.put("/api/plants/{plant_id}/zone-assignments/{user_id}")
async def set_zone_assignments(
    plant_id: int,
    user_id: int,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    admin = legacy.require_user(irrigation_manager_session)
    if admin["global_role"] != "admin":
        raise HTTPException(403)
    requested = payload.get("zones", [])
    with legacy.db() as conn:
        assignment = conn.execute(
            "SELECT role FROM user_plants WHERE user_id=? AND plant_id=?", (user_id, plant_id)
        ).fetchone()
        if not assignment:
            raise HTTPException(400, "Assegnare prima l'utente all'impianto")
        valid_zone_ids = {
            int(row["id"]) for row in conn.execute("SELECT id FROM zones WHERE plant_id=?", (plant_id,))
        }
        conn.execute(
            "DELETE FROM user_zone_assignments WHERE user_id=? AND zone_id IN (SELECT id FROM zones WHERE plant_id=?)",
            (user_id, plant_id),
        )
        for item in requested:
            zone_id = int(item["zone_id"])
            if zone_id not in valid_zone_ids:
                raise HTTPException(400, f"Zona {zone_id} non appartenente all'impianto")
            conn.execute(
                """INSERT INTO user_zone_assignments(
                     user_id,zone_id,can_view,can_start,can_program,assigned_by,assigned_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    user_id,
                    zone_id,
                    1 if item.get("can_view", True) else 0,
                    1 if item.get("can_start", True) else 0,
                    1 if item.get("can_program", True) else 0,
                    admin["id"],
                    legacy.iso(),
                ),
            )
        conn.commit()
    legacy.audit(
        admin["id"],
        "update_zone_assignments",
        plant_id,
        {"user_id": user_id, "zones": requested},
    )
    return {"ok": True}


@app.get("/api/plants/{plant_id}/my-zones")
async def my_zones(plant_id: int, irrigation_manager_session: str | None = Cookie(None)):
    user = legacy.require_user(irrigation_manager_session)
    access = ORIGINAL_PLANT_ACCESS(user, plant_id, "view")
    with legacy.db() as conn:
        if user["global_role"] == "admin" or access["role"] == "owner":
            rows = conn.execute("SELECT *,1 can_view,1 can_start,1 can_program FROM zones WHERE plant_id=? ORDER BY name", (plant_id,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT z.*,uza.can_view,uza.can_start,uza.can_program
                   FROM zones z JOIN user_zone_assignments uza ON uza.zone_id=z.id
                   WHERE z.plant_id=? AND uza.user_id=? AND uza.can_view=1 ORDER BY z.name""",
                (plant_id, user["id"]),
            ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/version-v040")
async def version_v040():
    return {"version": APP_VERSION, "billing_model": "per_user", "portal_port": 8100}
