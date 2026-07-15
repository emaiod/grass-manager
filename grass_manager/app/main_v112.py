from __future__ import annotations

from typing import Any

from fastapi import Cookie, HTTPException

import main_v111 as v111

app = v111.app
core = v111.core
APP_VERSION = "1.1.2"


def remove_route(path: str, methods: set[str]) -> None:
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and methods.intersection(getattr(route, "methods", set())))
    ]


remove_route("/api/plants/{plant_id}/zones", {"POST"})


@app.post("/api/plants/{plant_id}/zones")
async def add_zone_admin_only(
    plant_id: int,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    user = core.require_user(irrigation_manager_session)
    if user["global_role"] != "admin":
        raise HTTPException(403, "Solo l'amministratore può creare nuove zone")

    valve = str(payload.get("valve_entity", "")).strip()
    if not valve:
        raise HTTPException(400, "Selezionare una valvola")
    moisture = str(payload.get("moisture_entity", "")).strip() or None

    with core.db() as conn:
        if not conn.execute("SELECT 1 FROM plants WHERE id=?", (plant_id,)).fetchone():
            raise HTTPException(404, "Impianto non trovato")
        cur = conn.execute(
            "INSERT INTO zones(plant_id,name,valve_entity,moisture_entity,moisture_max,enabled,max_minutes) VALUES(?,?,?,?,?,1,?)",
            (plant_id, payload["name"], valve, moisture, payload.get("moisture_max"), int(payload.get("max_minutes", 60))),
        )
        v111.v110.register_entity(conn, plant_id, valve, f"Valvola · {payload['name']}", "valve", True)
        v111.v110.register_entity(conn, plant_id, moisture, f"Umidità · {payload['name']}", "moisture", False)
        conn.commit()

    core.audit(user["id"], "create_zone", plant_id, {"zone_id": cur.lastrowid})
    return {"id": cur.lastrowid}


@app.get("/api/version-v112")
async def version_v112():
    return {"version": APP_VERSION, "features": ["admin_without_plant_assignments_ui", "admin_only_zone_creation"]}
