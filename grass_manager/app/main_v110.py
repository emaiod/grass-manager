from __future__ import annotations

import json
from typing import Any

from fastapi import Cookie, HTTPException

import main_v100 as v100

app = v100.app
core = v100.core
APP_VERSION = "1.1.0"


def remove_route(path: str, methods: set[str]) -> None:
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and methods.intersection(getattr(route, "methods", set())))
    ]


for path, methods in [
    ("/api/plants/{plant_id}/details", {"GET"}),
    ("/api/plants/{plant_id}/zones", {"POST"}),
    ("/api/plants/{plant_id}/programs", {"POST"}),
]:
    remove_route(path, methods)


def register_entity(conn, plant_id: int, entity_id: str | None, label: str, kind: str, required: bool = True) -> None:
    if not entity_id:
        return
    conn.execute(
        """INSERT INTO plant_entities(plant_id,entity_id,label,kind,required,enabled)
           VALUES(?,?,?,?,?,1)
           ON CONFLICT(plant_id,entity_id) DO UPDATE SET
             label=excluded.label,kind=excluded.kind,required=excluded.required,enabled=1""",
        (plant_id, entity_id, label, kind, 1 if required else 0),
    )


@app.get("/api/plants/{plant_id}/details")
async def plant_details(plant_id: int, irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    access = core.plant_access(user, plant_id)
    with core.db() as conn:
        plant = core.rowdict(conn.execute("SELECT * FROM plants WHERE id=?", (plant_id,)).fetchone())
        if not plant:
            raise HTTPException(404, "Impianto non trovato")
        zones = [dict(r) for r in conn.execute("SELECT * FROM zones WHERE plant_id=? ORDER BY name", (plant_id,))]
        programs = []
        for row in conn.execute("SELECT * FROM programs WHERE plant_id=? ORDER BY name", (plant_id,)):
            program = dict(row)
            program["weekdays"] = json.loads(program["weekdays"] or "[]")
            program["start_times"] = json.loads(program["start_times"] or "[]")
            program["steps"] = [dict(x) for x in conn.execute(
                "SELECT ps.*,z.name zone_name FROM program_steps ps JOIN zones z ON z.id=ps.zone_id WHERE ps.program_id=? ORDER BY ps.position",
                (program["id"],),
            )]
            programs.append(program)
    return {"plant": plant, "zones": zones, "programs": programs, "access": access}


@app.post("/api/plants/{plant_id}/zones")
async def add_zone(plant_id: int, payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    core.plant_access(user, plant_id, "edit_programs")
    valve = str(payload.get("valve_entity", "")).strip()
    if not valve:
        raise HTTPException(400, "Selezionare una valvola")
    moisture = str(payload.get("moisture_entity", "")).strip() or None
    with core.db() as conn:
        cur = conn.execute(
            "INSERT INTO zones(plant_id,name,valve_entity,moisture_entity,moisture_max,enabled,max_minutes) VALUES(?,?,?,?,?,1,?)",
            (plant_id, payload["name"], valve, moisture, payload.get("moisture_max"), int(payload.get("max_minutes", 60))),
        )
        register_entity(conn, plant_id, valve, f"Valvola · {payload['name']}", "valve", True)
        register_entity(conn, plant_id, moisture, f"Umidità · {payload['name']}", "moisture", False)
        conn.commit()
    core.audit(user["id"], "create_zone", plant_id, {"zone_id": cur.lastrowid})
    return {"id": cur.lastrowid}


@app.delete("/api/plants/{plant_id}/zones/{zone_id}")
async def delete_zone(plant_id: int, zone_id: int, irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    core.plant_access(user, plant_id, "edit_programs")
    if core.runtime.state.get("running") and core.runtime.state.get("zone_id") == zone_id:
        raise HTTPException(409, "La zona è attualmente in esecuzione")
    with core.db() as conn:
        zone = conn.execute("SELECT * FROM zones WHERE id=? AND plant_id=?", (zone_id, plant_id)).fetchone()
        if not zone:
            raise HTTPException(404, "Zona non trovata")
        conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
        for entity_id in (zone["valve_entity"], zone["moisture_entity"]):
            if entity_id:
                used = conn.execute(
                    "SELECT 1 FROM zones WHERE plant_id=? AND (valve_entity=? OR moisture_entity=?) LIMIT 1",
                    (plant_id, entity_id, entity_id),
                ).fetchone()
                if not used:
                    conn.execute("DELETE FROM plant_entities WHERE plant_id=? AND entity_id=?", (plant_id, entity_id))
        conn.commit()
    core.audit(user["id"], "delete_zone", plant_id, {"zone_id": zone_id})
    return {"ok": True}


@app.post("/api/plants/{plant_id}/programs")
async def add_program(plant_id: int, payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    core.plant_access(user, plant_id, "edit_programs")
    steps = payload.get("steps", [])
    if not steps:
        raise HTTPException(400, "Selezionare almeno una zona")
    with core.db() as conn:
        valid = {r["id"] for r in conn.execute("SELECT id FROM zones WHERE plant_id=?", (plant_id,))}
        if any(int(s["zone_id"]) not in valid for s in steps):
            raise HTTPException(400, "Una zona non appartiene all'impianto")
        cur = conn.execute(
            """INSERT INTO programs(plant_id,name,enabled,weekdays,start_times,solar_event,solar_offset,pump_entity,pump_lead_seconds,pump_lag_seconds,inter_zone_seconds,weather_entity,skip_rain,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (plant_id, payload["name"], 1 if payload.get("enabled", True) else 0,
             json.dumps(payload.get("weekdays", [])), json.dumps(payload.get("start_times", [])),
             payload.get("solar_event"), int(payload.get("solar_offset", 0)), payload.get("pump_entity") or None,
             int(payload.get("pump_lead_seconds", 0)), int(payload.get("pump_lag_seconds", 0)),
             int(payload.get("inter_zone_seconds", 5)), payload.get("weather_entity") or None,
             1 if payload.get("skip_rain") else 0, core.iso()),
        )
        program_id = cur.lastrowid
        for position, step in enumerate(steps):
            conn.execute("INSERT INTO program_steps(program_id,zone_id,position,duration_minutes) VALUES(?,?,?,?)",
                         (program_id, int(step["zone_id"]), position, int(step["duration_minutes"])))
        register_entity(conn, plant_id, payload.get("pump_entity"), f"Pompa · {payload['name']}", "pump", True)
        register_entity(conn, plant_id, payload.get("weather_entity"), f"Meteo · {payload['name']}", "weather", False)
        conn.commit()
    core.audit(user["id"], "create_program", plant_id, {"program_id": program_id})
    return {"id": program_id}


@app.delete("/api/plants/{plant_id}/programs/{program_id}")
async def delete_program(plant_id: int, program_id: int, irrigation_manager_session: str | None = Cookie(None)):
    user = core.require_user(irrigation_manager_session)
    core.plant_access(user, plant_id, "edit_programs")
    if core.runtime.state.get("running") and core.runtime.state.get("program_id") == program_id:
        raise HTTPException(409, "Il programma è attualmente in esecuzione")
    with core.db() as conn:
        program = conn.execute("SELECT * FROM programs WHERE id=? AND plant_id=?", (program_id, plant_id)).fetchone()
        if not program:
            raise HTTPException(404, "Programma non trovato")
        conn.execute("DELETE FROM programs WHERE id=?", (program_id,))
        for entity_id in (program["pump_entity"], program["weather_entity"]):
            if entity_id:
                used = conn.execute(
                    "SELECT 1 FROM programs WHERE plant_id=? AND (pump_entity=? OR weather_entity=?) LIMIT 1",
                    (plant_id, entity_id, entity_id),
                ).fetchone()
                if not used:
                    conn.execute("DELETE FROM plant_entities WHERE plant_id=? AND entity_id=?", (plant_id, entity_id))
        conn.commit()
    core.audit(user["id"], "delete_program", plant_id, {"program_id": program_id})
    return {"ok": True}


@app.delete("/api/plants/{plant_id}")
async def delete_plant(plant_id: int, irrigation_manager_session: str | None = Cookie(None)):
    admin = v100.require_admin(irrigation_manager_session)
    if core.runtime.state.get("running") and core.runtime.state.get("plant_id") == plant_id:
        raise HTTPException(409, "L'impianto ha un'irrigazione in esecuzione")
    with core.db() as conn:
        plant = conn.execute("SELECT name FROM plants WHERE id=?", (plant_id,)).fetchone()
        if not plant:
            raise HTTPException(404, "Impianto non trovato")
        conn.execute("DELETE FROM plants WHERE id=?", (plant_id,))
        conn.commit()
    core.audit(admin["id"], "delete_plant", plant_id, {"name": plant["name"]})
    return {"ok": True}


@app.get("/api/version-v110")
async def version_v110():
    return {"version": APP_VERSION, "features": ["automatic_entities", "delete_plants", "delete_zones", "delete_programs", "responsive_dialogs"]}
