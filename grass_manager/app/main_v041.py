from __future__ import annotations

import json
from typing import Any

from fastapi import Cookie, HTTPException

import main_v040 as v040

app = v040.app
legacy = v040.legacy
APP_VERSION = "0.4.1"


def require_admin(token: str | None) -> dict[str, Any]:
    user = legacy.require_user(token)
    if user["global_role"] != "admin":
        raise HTTPException(403, "Accesso amministratore richiesto")
    return user


@app.get("/api/admin/subscriptions")
async def admin_subscriptions(irrigation_manager_session: str | None = Cookie(None)):
    require_admin(irrigation_manager_session)
    with legacy.db() as conn:
        users = conn.execute(
            """SELECT u.id,u.username,u.display_name,u.email,u.global_role,u.active,
                      us.plan_code,sp.name plan_name,us.status subscription_status,
                      us.monthly_price,us.current_period_start,us.current_period_end,
                      us.grace_until,us.cancel_at_period_end,us.provider,us.notes,
                      CASE WHEN EXISTS(
                        SELECT 1 FROM user_plants up WHERE up.user_id=u.id AND up.role='owner'
                      ) THEN 1 ELSE 0 END billing_required
                 FROM users u
                 LEFT JOIN user_subscriptions us ON us.user_id=u.id
                 LEFT JOIN subscription_plans sp ON sp.code=us.plan_code
                ORDER BY billing_required DESC,u.display_name"""
        ).fetchall()
        result = []
        for row in users:
            item = dict(row)
            item["plants"] = [
                dict(p)
                for p in conn.execute(
                    """SELECT p.id,p.name,up.role
                         FROM user_plants up JOIN plants p ON p.id=up.plant_id
                        WHERE up.user_id=? ORDER BY p.name""",
                    (item["id"],),
                )
            ]
            item["payments"] = [
                dict(p)
                for p in conn.execute(
                    """SELECT id,provider,amount,currency,status,paid_at,period_start,period_end,created_at
                         FROM payment_transactions WHERE user_id=?
                        ORDER BY created_at DESC LIMIT 10""",
                    (item["id"],),
                )
            ]
            result.append(item)
    return result


@app.put("/api/admin/plans/{plan_code}")
async def update_plan(
    plan_code: str,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    admin = require_admin(irrigation_manager_session)
    code = plan_code.strip().lower()
    if code not in {"base", "premium"}:
        raise HTTPException(400, "Piano non modificabile")
    try:
        price = float(payload.get("monthly_price"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Prezzo non valido")
    if price < 0:
        raise HTTPException(400, "Prezzo non valido")
    name = str(payload.get("name", "")).strip() or code.title()
    features = payload.get("features", {})
    if not isinstance(features, dict):
        raise HTTPException(400, "Funzioni piano non valide")
    with legacy.db() as conn:
        conn.execute(
            """UPDATE subscription_plans
                  SET name=?,monthly_price=?,features=?,active=?,updated_at=?
                WHERE code=?""",
            (
                name,
                price,
                json.dumps(features, ensure_ascii=False),
                1 if payload.get("active", True) else 0,
                legacy.iso(),
                code,
            ),
        )
        conn.commit()
    legacy.audit(admin["id"], "update_subscription_plan", details={"plan_code": code, "price": price})
    return {"ok": True}


@app.put("/api/admin/users/{user_id}")
async def update_user(
    user_id: int,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    admin = require_admin(irrigation_manager_session)
    if user_id == admin["id"] and payload.get("active") is False:
        raise HTTPException(400, "Non puoi disattivare il tuo account")
    username = str(payload.get("username", "")).strip().lower()
    display_name = str(payload.get("display_name", "")).strip()
    if not username or not display_name:
        raise HTTPException(400, "Username e nome sono obbligatori")
    global_role = str(payload.get("global_role", "user"))
    if global_role not in {"user", "admin"}:
        raise HTTPException(400, "Ruolo globale non valido")
    with legacy.db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Utente non trovato")
        duplicate = conn.execute(
            "SELECT id FROM users WHERE lower(username)=? AND id<>?", (username, user_id)
        ).fetchone()
        if duplicate:
            raise HTTPException(409, "Nome utente già esistente")
        conn.execute(
            """UPDATE users SET username=?,display_name=?,email=?,global_role=?,active=? WHERE id=?""",
            (
                username,
                display_name,
                payload.get("email") or None,
                global_role,
                1 if payload.get("active", True) else 0,
                user_id,
            ),
        )
        password = str(payload.get("password", ""))
        if password:
            try:
                password_hash = legacy.hash_password(password)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            conn.execute(
                "UPDATE users SET password_hash=?,force_password_change=1 WHERE id=?",
                (password_hash, user_id),
            )
        if "assignments" in payload:
            conn.execute("DELETE FROM user_plants WHERE user_id=?", (user_id,))
            for assignment in payload.get("assignments", []):
                role = assignment.get("role", "viewer")
                if role not in {"owner", "gardener", "maintainer", "viewer"}:
                    raise HTTPException(400, "Ruolo impianto non valido")
                conn.execute(
                    "INSERT INTO user_plants(user_id,plant_id,role,permissions) VALUES(?,?,?,?)",
                    (user_id, int(assignment["plant_id"]), role, "{}"),
                )
        conn.commit()
    legacy.audit(admin["id"], "update_user", details={"user_id": user_id})
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: int, irrigation_manager_session: str | None = Cookie(None)):
    admin = require_admin(irrigation_manager_session)
    if user_id == admin["id"]:
        raise HTTPException(400, "Non puoi eliminare il tuo account")
    with legacy.db() as conn:
        user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404, "Utente non trovato")
        running = legacy.runtime.state.get("running") and legacy.runtime.state.get("plant_id")
        if running:
            assigned = conn.execute(
                "SELECT 1 FROM user_plants WHERE user_id=? AND plant_id=?",
                (user_id, legacy.runtime.state.get("plant_id")),
            ).fetchone()
            if assigned:
                raise HTTPException(409, "Utente collegato a un impianto attualmente in esecuzione")
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    legacy.audit(admin["id"], "delete_user", details={"user_id": user_id, "username": user["username"]})
    return {"ok": True}


@app.get("/api/version-v041")
async def version_v041():
    return {"version": APP_VERSION, "features": ["subscriptions_page", "user_edit", "user_delete"]}
