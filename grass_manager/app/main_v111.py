from __future__ import annotations

import json
from typing import Any

from fastapi import Cookie, HTTPException

import main_v110 as v110

app = v110.app
core = v110.core
APP_VERSION = "1.1.1"


@app.put("/api/plants/{plant_id}/programs/{program_id}")
async def update_program(
    plant_id: int,
    program_id: int,
    payload: dict[str, Any],
    irrigation_manager_session: str | None = Cookie(None),
):
    user = core.require_user(irrigation_manager_session)
    core.plant_access(user, plant_id, "edit_programs")
    if core.runtime.state.get("running") and core.runtime.state.get("program_id") == program_id:
        raise HTTPException(409, "Il programma è attualmente in esecuzione")

    steps = payload.get("steps", [])
    if not steps:
        raise HTTPException(400, "Selezionare almeno una zona")

    times = payload.get("start_times", [])
    for value in times:
        try:
            hour, minute = map(int, str(value).split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, f"Orario non valido: {value}")

    with core.db() as conn:
        existing = conn.execute(
            "SELECT * FROM programs WHERE id=? AND plant_id=?", (program_id, plant_id)
        ).fetchone()
        if not existing:
            raise HTTPException(404, "Programma non trovato")

        valid_zones = {
            int(row["id"]) for row in conn.execute("SELECT id FROM zones WHERE plant_id=?", (plant_id,))
        }
        if any(int(step["zone_id"]) not in valid_zones for step in steps):
            raise HTTPException(400, "Una zona non appartiene all'impianto")

        old_entities = {existing["pump_entity"], existing["weather_entity"]} - {None, ""}
        conn.execute(
            """UPDATE programs SET name=?,enabled=?,weekdays=?,start_times=?,solar_event=?,solar_offset=?,
               pump_entity=?,pump_lead_seconds=?,pump_lag_seconds=?,inter_zone_seconds=?,weather_entity=?,skip_rain=?
               WHERE id=? AND plant_id=?""",
            (
                payload["name"],
                1 if payload.get("enabled", True) else 0,
                json.dumps(payload.get("weekdays", [])),
                json.dumps(times),
                payload.get("solar_event") or None,
                int(payload.get("solar_offset", 0)),
                payload.get("pump_entity") or None,
                int(payload.get("pump_lead_seconds", 0)),
                int(payload.get("pump_lag_seconds", 0)),
                int(payload.get("inter_zone_seconds", 5)),
                payload.get("weather_entity") or None,
                1 if payload.get("skip_rain") else 0,
                program_id,
                plant_id,
            ),
        )
        conn.execute("DELETE FROM program_steps WHERE program_id=?", (program_id,))
        for position, step in enumerate(steps):
            duration = int(step["duration_minutes"])
            if duration < 1:
                raise HTTPException(400, "La durata di ogni zona deve essere almeno 1 minuto")
            conn.execute(
                "INSERT INTO program_steps(program_id,zone_id,position,duration_minutes) VALUES(?,?,?,?)",
                (program_id, int(step["zone_id"]), position, duration),
            )

        v110.register_entity(conn, plant_id, payload.get("pump_entity"), f"Pompa · {payload['name']}", "pump", True)
        v110.register_entity(conn, plant_id, payload.get("weather_entity"), f"Meteo · {payload['name']}", "weather", False)

        new_entities = {payload.get("pump_entity"), payload.get("weather_entity")} - {None, ""}
        for entity_id in old_entities - new_entities:
            used = conn.execute(
                "SELECT 1 FROM programs WHERE plant_id=? AND id<>? AND (pump_entity=? OR weather_entity=?) LIMIT 1",
                (plant_id, program_id, entity_id, entity_id),
            ).fetchone()
            if not used:
                conn.execute("DELETE FROM plant_entities WHERE plant_id=? AND entity_id=?", (plant_id, entity_id))
        conn.commit()

    core.audit(user["id"], "update_program", plant_id, {"program_id": program_id})
    return {"ok": True}


@app.get("/api/version-v111")
async def version_v111():
    return {"version": APP_VERSION, "features": ["responsive_zone_dialog", "responsive_program_dialog", "edit_program"]}
