from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_VERSION = "0.3.0"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "irrigation_manager.db"
STATIC_DIR = Path(__file__).parent / "static"
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN", "")
HA_BASE = "http://supervisor/core/api"
SESSION_COOKIE = "irrigation_manager_session"
SESSION_DAYS = 30

DATA_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Irrigation Manager", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 8:
        raise ValueError("La password deve avere almeno 8 caratteri")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE NOT NULL,
              display_name TEXT NOT NULL,
              email TEXT,
              password_hash TEXT NOT NULL,
              global_role TEXT NOT NULL DEFAULT 'user',
              active INTEGER NOT NULL DEFAULT 1,
              force_password_change INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plants(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              customer_name TEXT,
              address TEXT,
              timezone TEXT NOT NULL DEFAULT 'Europe/Rome',
              enabled INTEGER NOT NULL DEFAULT 1,
              notes TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_plants(
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              permissions TEXT NOT NULL DEFAULT '{}',
              PRIMARY KEY(user_id, plant_id)
            );
            CREATE TABLE IF NOT EXISTS plant_entities(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
              entity_id TEXT NOT NULL,
              label TEXT NOT NULL,
              kind TEXT NOT NULL,
              required INTEGER NOT NULL DEFAULT 1,
              enabled INTEGER NOT NULL DEFAULT 1,
              last_state TEXT,
              last_seen_at TEXT,
              UNIQUE(plant_id, entity_id)
            );
            CREATE TABLE IF NOT EXISTS zones(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              valve_entity TEXT NOT NULL,
              moisture_entity TEXT,
              moisture_max REAL,
              enabled INTEGER NOT NULL DEFAULT 1,
              max_minutes INTEGER NOT NULL DEFAULT 60
            );
            CREATE TABLE IF NOT EXISTS programs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              weekdays TEXT NOT NULL DEFAULT '[]',
              start_times TEXT NOT NULL DEFAULT '[]',
              solar_event TEXT,
              solar_offset INTEGER NOT NULL DEFAULT 0,
              pump_entity TEXT,
              pump_lead_seconds INTEGER NOT NULL DEFAULT 0,
              pump_lag_seconds INTEGER NOT NULL DEFAULT 0,
              inter_zone_seconds INTEGER NOT NULL DEFAULT 5,
              weather_entity TEXT,
              skip_rain INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS program_steps(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
              zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
              position INTEGER NOT NULL,
              duration_minutes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              plant_id INTEGER REFERENCES plants(id) ON DELETE CASCADE,
              severity TEXT NOT NULL,
              code TEXT NOT NULL,
              title TEXT NOT NULL,
              message TEXT NOT NULL,
              entity_id TEXT,
              status TEXT NOT NULL DEFAULT 'open',
              created_at TEXT NOT NULL,
              acknowledged_at TEXT,
              acknowledged_by INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS irrigation_logs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
              program_id INTEGER,
              program_name TEXT,
              zone_id INTEGER,
              zone_name TEXT,
              source TEXT NOT NULL,
              status TEXT NOT NULL,
              planned_seconds INTEGER,
              actual_seconds INTEGER,
              actor_user_id INTEGER,
              message TEXT,
              started_at TEXT NOT NULL,
              ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              plant_id INTEGER,
              action TEXT NOT NULL,
              details TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS weather_history(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              plant_id INTEGER NOT NULL REFERENCES plants(id) ON DELETE CASCADE,
              entity_id TEXT,
              condition TEXT,
              temperature REAL,
              humidity REAL,
              pressure REAL,
              wind_speed REAL,
              precipitation REAL,
              recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions(
              plant_id INTEGER PRIMARY KEY REFERENCES plants(id) ON DELETE CASCADE,
              plan TEXT NOT NULL DEFAULT 'base',
              status TEXT NOT NULL DEFAULT 'trial',
              monthly_price REAL NOT NULL DEFAULT 0,
              renewal_date TEXT,
              notes TEXT
            );
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if count == 0:
            options = {}
            try:
                options = json.loads(Path("/data/options.json").read_text())
            except Exception:
                pass
            username = options.get("bootstrap_admin_username", "admin")
            password = options.get("bootstrap_admin_password", "change-me-now")
            conn.execute(
                "INSERT INTO users(username,display_name,password_hash,global_role,active,force_password_change,created_at) VALUES(?,?,?,?,?,?,?)",
                (username, "Amministratore", hash_password(password), "admin", 1, 1, iso()),
            )
        conn.commit()


init_db()


async def ha_request(method: str, path: str, **kwargs: Any) -> Any:
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN non disponibile")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {SUPERVISOR_TOKEN}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.request(method, f"{HA_BASE}{path}", headers=headers, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()


async def entity_state(entity_id: str) -> dict[str, Any] | None:
    try:
        return await ha_request("GET", f"/states/{entity_id}")
    except Exception:
        return None


async def entity_command(entity_id: str, turn_on: bool) -> None:
    domain = entity_id.split(".", 1)[0]
    if domain == "valve":
        service = "open_valve" if turn_on else "close_valve"
    elif domain in {"switch", "input_boolean", "light"}:
        service = "turn_on" if turn_on else "turn_off"
    else:
        raise RuntimeError(f"Dominio non comandabile: {domain}")
    await ha_request("POST", f"/services/{domain}/{service}", json={"entity_id": entity_id})


def create_alert(plant_id: int | None, severity: str, code: str, title: str, message: str, entity_id: str | None = None) -> None:
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM alerts WHERE plant_id IS ? AND code=? AND entity_id IS ? AND status='open'",
            (plant_id, code, entity_id),
        ).fetchone()
        if existing:
            conn.execute("UPDATE alerts SET message=?,severity=?,created_at=? WHERE id=?", (message, severity, iso(), existing["id"]))
        else:
            conn.execute(
                "INSERT INTO alerts(plant_id,severity,code,title,message,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (plant_id, severity, code, title, message, entity_id, iso()),
            )
        conn.commit()


async def notify_all(title: str, message: str) -> None:
    with db() as conn:
        enabled = conn.execute("SELECT value FROM settings WHERE key='notifications_enabled'").fetchone()
        service = conn.execute("SELECT value FROM settings WHERE key='notification_service'").fetchone()
    if not enabled or enabled["value"] != "1" or not service or "." not in service["value"]:
        return
    domain, action = service["value"].split(".", 1)
    try:
        await ha_request("POST", f"/services/{domain}/{action}", json={"title": title, "message": message})
    except Exception:
        pass


DEFAULT_PERMISSIONS = {
    "owner": ["view", "start", "stop", "skip", "edit_programs", "view_history", "view_alerts"],
    "gardener": ["view", "start", "stop", "skip", "view_history", "view_alerts"],
    "maintainer": ["view", "start", "stop", "skip", "test_entities", "view_history", "view_alerts", "ack_alerts"],
    "viewer": ["view", "view_history", "view_alerts"],
}


def user_from_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
            (token_hash, iso()),
        ).fetchone()
    return rowdict(row)


def require_user(token: str | None) -> dict[str, Any]:
    user = user_from_token(token)
    if not user:
        raise HTTPException(401, "Accesso richiesto")
    return user


def accessible_plant_ids(user: dict[str, Any]) -> list[int] | None:
    if user["global_role"] == "admin":
        return None
    with db() as conn:
        return [r["plant_id"] for r in conn.execute("SELECT plant_id FROM user_plants WHERE user_id=?", (user["id"],))]


def plant_access(user: dict[str, Any], plant_id: int, permission: str = "view") -> dict[str, Any]:
    if user["global_role"] == "admin":
        return {"role": "admin", "permissions": ["*"]}
    with db() as conn:
        row = conn.execute("SELECT role,permissions FROM user_plants WHERE user_id=? AND plant_id=?", (user["id"], plant_id)).fetchone()
    if not row:
        raise HTTPException(403, "Impianto non assegnato")
    custom = json.loads(row["permissions"] or "{}")
    permissions = custom.get("allow") or DEFAULT_PERMISSIONS.get(row["role"], ["view"])
    if permission not in permissions:
        raise HTTPException(403, "Permesso insufficiente")
    return {"role": row["role"], "permissions": permissions}


def audit(user_id: int | None, action: str, plant_id: int | None = None, details: Any = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_logs(user_id,plant_id,action,details,created_at) VALUES(?,?,?,?,?)",
            (user_id, plant_id, action, json.dumps(details, ensure_ascii=False) if details is not None else None, iso()),
        )
        conn.commit()


class Runtime:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.skip_event = asyncio.Event()
        self.future_skips: set[int] = set()
        self.state: dict[str, Any] = {"running": False, "plant_id": None, "program_id": None, "steps": [], "remaining_seconds": 0}


runtime = Runtime()
solar_cache: dict[str, datetime] = {}
last_weather_hour: str | None = None


async def run_program(program_id: int, source: str, actor_user_id: int | None) -> None:
    runtime.stop_event.clear(); runtime.skip_event.clear(); runtime.future_skips.clear()
    with db() as conn:
        p = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
        if not p:
            return
        program = dict(p)
        plant = conn.execute("SELECT * FROM plants WHERE id=?", (program["plant_id"],)).fetchone()
        steps = [dict(r) for r in conn.execute(
            """SELECT ps.*,z.name zone_name,z.valve_entity,z.moisture_entity,z.moisture_max,z.enabled zone_enabled,z.max_minutes
               FROM program_steps ps JOIN zones z ON z.id=ps.zone_id WHERE ps.program_id=? ORDER BY ps.position""", (program_id,))]
    runtime.state = {"running": True, "plant_id": program["plant_id"], "plant_name": plant["name"], "program_id": program_id,
                     "program_name": program["name"], "zone_id": None, "zone_name": None, "remaining_seconds": 0,
                     "steps": [{"zone_id": s["zone_id"], "zone_name": s["zone_name"], "duration_minutes": s["duration_minutes"], "status": "pending"} for s in steps], "last_error": None}
    await notify_all("Irrigazione avviata", f"{plant['name']}: programma {program['name']} avviato")
    pump_on = False
    current_valve: str | None = None
    try:
        if program["pump_entity"]:
            st = await entity_state(program["pump_entity"])
            if not st or st.get("state") in {"unavailable", "unknown"}:
                raise RuntimeError(f"Pompa non disponibile: {program['pump_entity']}")
            await entity_command(program["pump_entity"], True); pump_on = True
            await asyncio.sleep(max(0, program["pump_lead_seconds"]))
        for index, step in enumerate(steps):
            if runtime.stop_event.is_set(): break
            if index in runtime.future_skips:
                runtime.state["steps"][index]["status"] = "skipped"; continue
            if not step["zone_enabled"]:
                runtime.state["steps"][index]["status"] = "disabled"; continue
            st = await entity_state(step["valve_entity"])
            if not st or st.get("state") in {"unavailable", "unknown"}:
                raise RuntimeError(f"Valvola non disponibile: {step['valve_entity']}")
            if step["moisture_entity"] and step["moisture_max"] is not None:
                moisture = await entity_state(step["moisture_entity"])
                try:
                    if moisture and float(moisture["state"]) >= float(step["moisture_max"]):
                        runtime.state["steps"][index]["status"] = "skipped"; continue
                except Exception:
                    pass
            duration = min(int(step["duration_minutes"]), int(step["max_minutes"])) * 60
            runtime.state.update({"zone_id": step["zone_id"], "zone_name": step["zone_name"], "current_step_index": index})
            runtime.state["steps"][index]["status"] = "running"
            started = utcnow(); current_valve = step["valve_entity"]
            await entity_command(current_valve, True)
            for remaining in range(duration, 0, -1):
                runtime.state["remaining_seconds"] = remaining
                if runtime.stop_event.is_set() or runtime.skip_event.is_set(): break
                await asyncio.sleep(1)
            await entity_command(current_valve, False); current_valve = None
            actual = int((utcnow() - started).total_seconds())
            status = "stopped" if runtime.stop_event.is_set() else "skipped" if runtime.skip_event.is_set() else "completed"
            runtime.state["steps"][index]["status"] = status
            with db() as conn:
                conn.execute("""INSERT INTO irrigation_logs(plant_id,program_id,program_name,zone_id,zone_name,source,status,planned_seconds,actual_seconds,actor_user_id,started_at,ended_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (program["plant_id"], program_id, program["name"], step["zone_id"], step["zone_name"], source, status, duration, actual, actor_user_id, iso(started), iso()))
                conn.commit()
            runtime.skip_event.clear()
            if runtime.stop_event.is_set(): break
            await asyncio.sleep(max(0, program["inter_zone_seconds"]))
        if not runtime.stop_event.is_set():
            await notify_all("Irrigazione completata", f"{plant['name']}: programma {program['name']} terminato correttamente")
    except Exception as exc:
        runtime.state["last_error"] = str(exc)
        create_alert(program["plant_id"], "critical", "irrigation_error", "Errore irrigazione", str(exc), current_valve or program.get("pump_entity"))
        await notify_all("Errore irrigazione", f"{plant['name']}: {exc}")
    finally:
        if current_valve:
            try: await entity_command(current_valve, False)
            except Exception: pass
        if pump_on and program["pump_entity"]:
            await asyncio.sleep(max(0, program["pump_lag_seconds"]))
            try: await entity_command(program["pump_entity"], False)
            except Exception: pass
        runtime.state["running"] = False; runtime.state["remaining_seconds"] = 0


async def monitor_loop() -> None:
    global last_weather_hour
    await asyncio.sleep(5)
    while True:
        try:
            states = await ha_request("GET", "/states")
            state_map = {s["entity_id"]: s for s in states}
            missing = []
            with db() as conn:
                entities = [dict(r) for r in conn.execute("SELECT * FROM plant_entities WHERE enabled=1")]
                for e in entities:
                    st = state_map.get(e["entity_id"])
                    if st and st.get("state") not in {"unavailable", "unknown"}:
                        conn.execute("UPDATE plant_entities SET last_state=?,last_seen_at=? WHERE id=?", (st.get("state"), iso(), e["id"]))
                        conn.execute("UPDATE alerts SET status='resolved',acknowledged_at=? WHERE plant_id=? AND entity_id=? AND code='entity_offline' AND status='open'", (iso(), e["plant_id"], e["entity_id"]))
                    elif e["required"]:
                        missing.append(e)
                hour_key = datetime.now().strftime("%Y-%m-%d %H")
                if hour_key != last_weather_hour:
                    last_weather_hour = hour_key
                    for e in entities:
                        if e["kind"] != "weather":
                            continue
                        st = state_map.get(e["entity_id"])
                        if not st:
                            continue
                        a = st.get("attributes", {})
                        conn.execute("""INSERT INTO weather_history(plant_id,entity_id,condition,temperature,humidity,pressure,wind_speed,precipitation,recorded_at)
                                      VALUES(?,?,?,?,?,?,?,?,?)""", (e["plant_id"], e["entity_id"], st.get("state"), a.get("temperature"), a.get("humidity"), a.get("pressure"), a.get("wind_speed"), a.get("precipitation") or a.get("precipitation_probability"), iso()))
                conn.commit()
            for e in missing:
                create_alert(e["plant_id"], "critical", "entity_offline", "Dispositivo offline", f"Entità non disponibile: {e['label']}", e["entity_id"])
        except Exception as exc:
            create_alert(None, "critical", "ha_connection", "Home Assistant non raggiungibile", str(exc))
        await asyncio.sleep(60)


async def scheduler_loop() -> None:
    last_keys: set[str] = set()
    while True:
        now = datetime.now().astimezone()
        try:
            sun = await entity_state("sun.sun")
            if sun:
                attrs = sun.get("attributes", {})
                for event, attr in (("sunrise", "next_rising"), ("sunset", "next_setting")):
                    raw = attrs.get(attr)
                    if raw:
                        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
                        solar_cache[f"{event}:{dt.date().isoformat()}"] = dt
        except Exception:
            pass
        if not runtime.state["running"]:
            with db() as conn:
                programs = [dict(r) for r in conn.execute("SELECT * FROM programs WHERE enabled=1")]
            for p in programs:
                weekdays = json.loads(p["weekdays"] or "[]")
                if now.weekday() not in weekdays:
                    continue
                candidates: list[tuple[str, datetime]] = []
                for hhmm in json.loads(p["start_times"] or "[]"):
                    try:
                        h, m = map(int, hhmm.split(":")); candidates.append((f"time:{hhmm}", now.replace(hour=h, minute=m, second=0, microsecond=0)))
                    except Exception:
                        continue
                if p.get("solar_event"):
                    base = solar_cache.get(f"{p['solar_event']}:{now.date().isoformat()}")
                    if base:
                        candidates.append((p["solar_event"], base + timedelta(minutes=int(p.get("solar_offset") or 0))))
                for kind, candidate in candidates:
                    trigger_key = f"{p['id']}:{kind}:{candidate.isoformat(timespec='minutes')}"
                    if abs((now - candidate).total_seconds()) <= 40 and trigger_key not in last_keys:
                        last_keys.add(trigger_key)
                        runtime.task = asyncio.create_task(run_program(p["id"], "automatic", None))
                        break
                if runtime.state["running"] or (runtime.task and not runtime.task.done()):
                    break
        if len(last_keys) > 500:
            last_keys = {k for k in last_keys if now.date().isoformat() in k}
        await asyncio.sleep(15)


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(monitor_loop())
    asyncio.create_task(scheduler_loop())


@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/version")
async def version(): return {"version": APP_VERSION}


@app.post("/api/login")
async def login(payload: dict[str, Any], response: Response):
    username = str(payload.get("username", "")).strip().lower(); password = str(payload.get("password", ""))
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE lower(username)=? AND active=1", (username,)).fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            raise HTTPException(401, "Credenziali non valide")
        token = secrets.token_urlsafe(40); token_hash = hashlib.sha256(token.encode()).hexdigest(); expires = utcnow() + timedelta(days=SESSION_DAYS)
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash, user["id"], iso(expires), iso()))
        conn.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=False, max_age=SESSION_DAYS*86400, path="/")
    audit(user["id"], "login")
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response, irrigation_manager_session: str | None = Cookie(None)):
    if irrigation_manager_session:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(irrigation_manager_session.encode()).hexdigest(),)); conn.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me(irrigation_manager_session: str | None = Cookie(None)):
    u = require_user(irrigation_manager_session)
    return {k: u[k] for k in ("id", "username", "display_name", "email", "global_role", "force_password_change")}


@app.put("/api/me/password")
async def change_password(payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    u = require_user(irrigation_manager_session)
    if not verify_password(str(payload.get("current_password", "")), u["password_hash"]): raise HTTPException(400, "Password attuale errata")
    try: new_hash = hash_password(str(payload.get("new_password", "")))
    except ValueError as e: raise HTTPException(400, str(e))
    with db() as conn:
        conn.execute("UPDATE users SET password_hash=?,force_password_change=0 WHERE id=?", (new_hash, u["id"])); conn.commit()
    audit(u["id"], "change_password")
    return {"ok": True}


@app.get("/api/entities")
async def entities(irrigation_manager_session: str | None = Cookie(None)):
    require_user(irrigation_manager_session)
    states = await ha_request("GET", "/states")
    return [{"entity_id": s["entity_id"], "domain": s["entity_id"].split(".")[0], "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]), "state": s.get("state")} for s in states]


@app.get("/api/dashboard")
async def dashboard(irrigation_manager_session: str | None = Cookie(None)):
    u = require_user(irrigation_manager_session); ids = accessible_plant_ids(u)
    where = "" if ids is None else f" WHERE p.id IN ({','.join('?'*len(ids))})" if ids else " WHERE 1=0"
    params = [] if ids is None else ids
    with db() as conn:
        plants = [dict(r) for r in conn.execute(f"SELECT p.*,s.plan,s.status subscription_status FROM plants p LEFT JOIN subscriptions s ON s.plant_id=p.id{where} ORDER BY p.name", params)]
        for p in plants:
            p["open_alerts"] = conn.execute("SELECT COUNT(*) c FROM alerts WHERE plant_id=? AND status='open'", (p["id"],)).fetchone()["c"]
            p["offline_entities"] = conn.execute("SELECT COUNT(*) c FROM plant_entities WHERE plant_id=? AND required=1 AND (last_seen_at IS NULL OR last_state IN ('unavailable','unknown'))", (p["id"],)).fetchone()["c"]
            p["next_runs"] = []
            for pr in conn.execute("SELECT id,name,weekdays,start_times FROM programs WHERE plant_id=? AND enabled=1", (p["id"],)):
                p["next_runs"].append({"name": pr["name"], "weekdays": json.loads(pr["weekdays"]), "start_times": json.loads(pr["start_times"])})
    return {"plants": plants, "runtime": runtime.state, "totals": {"plants": len(plants), "alerts": sum(p["open_alerts"] for p in plants), "offline": sum(p["offline_entities"] for p in plants)}}


@app.get("/api/plants")
async def list_plants(irrigation_manager_session: str | None = Cookie(None)):
    u=require_user(irrigation_manager_session); ids=accessible_plant_ids(u)
    with db() as conn:
        if ids is None: rows=conn.execute("SELECT * FROM plants ORDER BY name").fetchall()
        elif ids: rows=conn.execute(f"SELECT * FROM plants WHERE id IN ({','.join('?'*len(ids))}) ORDER BY name", ids).fetchall()
        else: rows=[]
    return [dict(r) for r in rows]


@app.post("/api/plants")
async def create_plant(payload: dict[str, Any], irrigation_manager_session: str | None = Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": raise HTTPException(403)
    with db() as conn:
        cur=conn.execute("INSERT INTO plants(name,customer_name,address,timezone,enabled,notes,created_at) VALUES(?,?,?,?,?,?,?)",(payload["name"],payload.get("customer_name"),payload.get("address"),payload.get("timezone","Europe/Rome"),1,payload.get("notes"),iso()))
        pid=cur.lastrowid; conn.execute("INSERT INTO subscriptions(plant_id,plan,status,monthly_price) VALUES(?,?,?,?)",(pid,payload.get("plan","base"),payload.get("subscription_status","trial"),float(payload.get("monthly_price",0) or 0))); conn.commit()
    audit(u["id"],"create_plant",pid,payload); return {"id":pid}


@app.put("/api/plants/{plant_id}")
async def update_plant(plant_id:int,payload:dict[str,Any],irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": raise HTTPException(403)
    with db() as conn:
        conn.execute("UPDATE plants SET name=?,customer_name=?,address=?,timezone=?,enabled=?,notes=? WHERE id=?",(payload["name"],payload.get("customer_name"),payload.get("address"),payload.get("timezone","Europe/Rome"),1 if payload.get("enabled",True) else 0,payload.get("notes"),plant_id))
        conn.execute("INSERT INTO subscriptions(plant_id,plan,status,monthly_price,renewal_date,notes) VALUES(?,?,?,?,?,?) ON CONFLICT(plant_id) DO UPDATE SET plan=excluded.plan,status=excluded.status,monthly_price=excluded.monthly_price,renewal_date=excluded.renewal_date,notes=excluded.notes",(plant_id,payload.get("plan","base"),payload.get("subscription_status","active"),float(payload.get("monthly_price",0) or 0),payload.get("renewal_date"),payload.get("subscription_notes"))); conn.commit()
    audit(u["id"],"update_plant",plant_id,payload); return {"ok":True}


@app.get("/api/plants/{plant_id}/details")
async def plant_details(plant_id:int,irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session); access=plant_access(u,plant_id)
    with db() as conn:
        plant=rowdict(conn.execute("SELECT p.*,s.plan,s.status subscription_status,s.monthly_price,s.renewal_date FROM plants p LEFT JOIN subscriptions s ON s.plant_id=p.id WHERE p.id=?",(plant_id,)).fetchone())
        entities=[dict(r) for r in conn.execute("SELECT * FROM plant_entities WHERE plant_id=? ORDER BY kind,label",(plant_id,))]
        zones=[dict(r) for r in conn.execute("SELECT * FROM zones WHERE plant_id=? ORDER BY name",(plant_id,))]
        programs=[]
        for r in conn.execute("SELECT * FROM programs WHERE plant_id=? ORDER BY name",(plant_id,)):
            p=dict(r); p["weekdays"]=json.loads(p["weekdays"]);p["start_times"]=json.loads(p["start_times"]);p["steps"]=[dict(x) for x in conn.execute("SELECT ps.*,z.name zone_name FROM program_steps ps JOIN zones z ON z.id=ps.zone_id WHERE ps.program_id=? ORDER BY ps.position",(p["id"],))];programs.append(p)
    return {"plant":plant,"entities":entities,"zones":zones,"programs":programs,"access":access}


@app.post("/api/plants/{plant_id}/entities")
async def add_entity(plant_id:int,payload:dict[str,Any],irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": plant_access(u,plant_id,"test_entities")
    with db() as conn:
        conn.execute("INSERT INTO plant_entities(plant_id,entity_id,label,kind,required,enabled) VALUES(?,?,?,?,?,1) ON CONFLICT(plant_id,entity_id) DO UPDATE SET label=excluded.label,kind=excluded.kind,required=excluded.required,enabled=1",(plant_id,payload["entity_id"],payload.get("label",payload["entity_id"]),payload.get("kind","other"),1 if payload.get("required",True) else 0));conn.commit()
    audit(u["id"],"add_entity",plant_id,payload);return {"ok":True}


@app.post("/api/plants/{plant_id}/zones")
async def add_zone(plant_id:int,payload:dict[str,Any],irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session); plant_access(u,plant_id,"edit_programs")
    with db() as conn:
        cur=conn.execute("INSERT INTO zones(plant_id,name,valve_entity,moisture_entity,moisture_max,enabled,max_minutes) VALUES(?,?,?,?,?,1,?)",(plant_id,payload["name"],payload["valve_entity"],payload.get("moisture_entity"),payload.get("moisture_max"),int(payload.get("max_minutes",60))));conn.commit()
    return {"id":cur.lastrowid}


@app.post("/api/plants/{plant_id}/programs")
async def add_program(plant_id:int,payload:dict[str,Any],irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session); plant_access(u,plant_id,"edit_programs")
    times=payload.get("start_times",[]);weekdays=payload.get("weekdays",[])
    for t in times:
        try: datetime.strptime(t,"%H:%M")
        except ValueError: raise HTTPException(400,f"Orario non valido: {t}")
    with db() as conn:
        cur=conn.execute("""INSERT INTO programs(plant_id,name,enabled,weekdays,start_times,solar_event,solar_offset,pump_entity,pump_lead_seconds,pump_lag_seconds,inter_zone_seconds,weather_entity,skip_rain,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(plant_id,payload["name"],1 if payload.get("enabled",True) else 0,json.dumps(weekdays),json.dumps(times),payload.get("solar_event"),int(payload.get("solar_offset",0)),payload.get("pump_entity"),int(payload.get("pump_lead_seconds",0)),int(payload.get("pump_lag_seconds",0)),int(payload.get("inter_zone_seconds",5)),payload.get("weather_entity"),1 if payload.get("skip_rain") else 0,iso()))
        pid=cur.lastrowid
        for i,s in enumerate(payload.get("steps",[])): conn.execute("INSERT INTO program_steps(program_id,zone_id,position,duration_minutes) VALUES(?,?,?,?)",(pid,int(s["zone_id"]),i,int(s["duration_minutes"])))
        conn.commit()
    audit(u["id"],"create_program",plant_id,{"program_id":pid});return {"id":pid}


@app.post("/api/programs/{program_id}/start")
async def start_program(program_id:int,irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    with db() as conn: p=conn.execute("SELECT plant_id FROM programs WHERE id=?",(program_id,)).fetchone()
    if not p: raise HTTPException(404)
    plant_access(u,p["plant_id"],"start")
    if runtime.state["running"]: raise HTTPException(409,"Un programma è già in esecuzione")
    runtime.task=asyncio.create_task(run_program(program_id,"manual",u["id"]));audit(u["id"],"start_program",p["plant_id"],{"program_id":program_id});return {"ok":True}


@app.post("/api/runtime/stop")
async def stop(irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if runtime.state.get("plant_id"): plant_access(u,int(runtime.state["plant_id"]),"stop")
    runtime.stop_event.set();audit(u["id"],"stop_program",runtime.state.get("plant_id"));return {"ok":True}


@app.post("/api/runtime/skip/{index}")
async def skip(index:int,irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if not runtime.state["running"]: raise HTTPException(409,"Nessun programma in esecuzione")
    plant_access(u,int(runtime.state["plant_id"]),"skip")
    steps=runtime.state.get("steps",[])
    if index<0 or index>=len(steps): raise HTTPException(404)
    if steps[index]["status"]=="running": runtime.skip_event.set()
    elif steps[index]["status"]=="pending": runtime.future_skips.add(index);steps[index]["status"]="skipped"
    else: raise HTTPException(409,"Zona non saltabile")
    audit(u["id"],"skip_zone",runtime.state.get("plant_id"),{"index":index});return {"ok":True}


@app.get("/api/runtime")
async def get_runtime(irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if runtime.state.get("plant_id"): plant_access(u,int(runtime.state["plant_id"]),"view")
    return runtime.state


@app.get("/api/alerts")
async def alerts(irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session);ids=accessible_plant_ids(u)
    with db() as conn:
        if ids is None: rows=conn.execute("SELECT a.*,p.name plant_name FROM alerts a LEFT JOIN plants p ON p.id=a.plant_id ORDER BY CASE a.status WHEN 'open' THEN 0 ELSE 1 END,a.created_at DESC LIMIT 500").fetchall()
        elif ids: rows=conn.execute(f"SELECT a.*,p.name plant_name FROM alerts a LEFT JOIN plants p ON p.id=a.plant_id WHERE a.plant_id IN ({','.join('?'*len(ids))}) ORDER BY CASE a.status WHEN 'open' THEN 0 ELSE 1 END,a.created_at DESC LIMIT 500",ids).fetchall()
        else: rows=[]
    return [dict(r) for r in rows]


@app.post("/api/alerts/{alert_id}/ack")
async def ack_alert(alert_id:int,irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    with db() as conn:
        a=conn.execute("SELECT plant_id FROM alerts WHERE id=?",(alert_id,)).fetchone()
        if not a: raise HTTPException(404)
        if a["plant_id"]: plant_access(u,a["plant_id"],"ack_alerts")
        elif u["global_role"]!="admin": raise HTTPException(403)
        conn.execute("UPDATE alerts SET status='acknowledged',acknowledged_at=?,acknowledged_by=? WHERE id=?",(iso(),u["id"],alert_id));conn.commit()
    return {"ok":True}


@app.get("/api/history")
async def history(plant_id:int|None=None,irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    ids=accessible_plant_ids(u)
    if plant_id is not None: plant_access(u,plant_id,"view_history")
    with db() as conn:
        if plant_id: rows=conn.execute("SELECT l.*,p.name plant_name FROM irrigation_logs l JOIN plants p ON p.id=l.plant_id WHERE l.plant_id=? ORDER BY l.started_at DESC LIMIT 500",(plant_id,)).fetchall()
        elif ids is None: rows=conn.execute("SELECT l.*,p.name plant_name FROM irrigation_logs l JOIN plants p ON p.id=l.plant_id ORDER BY l.started_at DESC LIMIT 500").fetchall()
        elif ids: rows=conn.execute(f"SELECT l.*,p.name plant_name FROM irrigation_logs l JOIN plants p ON p.id=l.plant_id WHERE l.plant_id IN ({','.join('?'*len(ids))}) ORDER BY l.started_at DESC LIMIT 500",ids).fetchall()
        else: rows=[]
    return [dict(r) for r in rows]


@app.get("/api/users")
async def users(irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": raise HTTPException(403)
    with db() as conn:
        result=[]
        for row in conn.execute("SELECT id,username,display_name,email,global_role,active,force_password_change,created_at FROM users ORDER BY display_name"):
            x=dict(row);x["plants"]=[dict(r) for r in conn.execute("SELECT up.plant_id,p.name,up.role,up.permissions FROM user_plants up JOIN plants p ON p.id=up.plant_id WHERE up.user_id=?",(x["id"],))];result.append(x)
    return result


@app.post("/api/users")
async def create_user(payload:dict[str,Any],irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": raise HTTPException(403)
    try: ph=hash_password(payload["password"])
    except ValueError as e: raise HTTPException(400,str(e))
    with db() as conn:
        try: cur=conn.execute("INSERT INTO users(username,display_name,email,password_hash,global_role,active,force_password_change,created_at) VALUES(?,?,?,?,?,1,1,?)",(payload["username"].lower(),payload["display_name"],payload.get("email"),ph,payload.get("global_role","user"),iso()))
        except sqlite3.IntegrityError: raise HTTPException(409,"Nome utente già esistente")
        uid=cur.lastrowid
        for a in payload.get("assignments",[]): conn.execute("INSERT INTO user_plants(user_id,plant_id,role,permissions) VALUES(?,?,?,?)",(uid,int(a["plant_id"]),a.get("role","viewer"),json.dumps({"allow":a.get("permissions",[])}) if a.get("permissions") else "{}"))
        conn.commit()
    audit(u["id"],"create_user",details={"user_id":uid});return {"id":uid}


@app.put("/api/users/{user_id}/assignments")
async def assignments(user_id:int,payload:dict[str,Any],irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": raise HTTPException(403)
    with db() as conn:
        conn.execute("DELETE FROM user_plants WHERE user_id=?",(user_id,))
        for a in payload.get("assignments",[]): conn.execute("INSERT INTO user_plants(user_id,plant_id,role,permissions) VALUES(?,?,?,?)",(user_id,int(a["plant_id"]),a.get("role","viewer"),json.dumps({"allow":a.get("permissions",[])}) if a.get("permissions") else "{}"))
        conn.commit()
    audit(u["id"],"update_assignments",details={"user_id":user_id});return {"ok":True}


@app.get("/api/audit")
async def audit_list(irrigation_manager_session:str|None=Cookie(None)):
    u=require_user(irrigation_manager_session)
    if u["global_role"]!="admin": raise HTTPException(403)
    with db() as conn: rows=conn.execute("SELECT a.*,u.display_name,p.name plant_name FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id LEFT JOIN plants p ON p.id=a.plant_id ORDER BY a.created_at DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/calendar")
async def calendar(days: int = 30, irrigation_manager_session: str | None = Cookie(None)):
    u = require_user(irrigation_manager_session); ids = accessible_plant_ids(u); now = datetime.now().astimezone(); events = []
    with db() as conn:
        if ids is None: programs = [dict(r) for r in conn.execute("SELECT pr.*,p.name plant_name FROM programs pr JOIN plants p ON p.id=pr.plant_id WHERE pr.enabled=1")]
        elif ids: programs = [dict(r) for r in conn.execute(f"SELECT pr.*,p.name plant_name FROM programs pr JOIN plants p ON p.id=pr.plant_id WHERE pr.enabled=1 AND pr.plant_id IN ({','.join('?'*len(ids))})", ids)]
        else: programs = []
    for p in programs:
        weekdays=json.loads(p["weekdays"] or "[]")
        for offset in range(max(1,min(days,90))):
            d=(now+timedelta(days=offset)).date()
            if d.weekday() not in weekdays: continue
            for hhmm in json.loads(p["start_times"] or "[]"):
                try:
                    h,m=map(int,hhmm.split(":")); dt=datetime.combine(d,datetime.min.time(),tzinfo=now.tzinfo).replace(hour=h,minute=m)
                    if dt>=now: events.append({"plant_id":p["plant_id"],"plant_name":p["plant_name"],"program_id":p["id"],"program_name":p["name"],"kind":"fixed","when":dt.isoformat()})
                except Exception: pass
            if p.get("solar_event"):
                base=solar_cache.get(f"{p['solar_event']}:{d.isoformat()}")
                if base:
                    dt=base+timedelta(minutes=int(p.get("solar_offset") or 0))
                    if dt>=now: events.append({"plant_id":p["plant_id"],"plant_name":p["plant_name"],"program_id":p["id"],"program_name":p["name"],"kind":p["solar_event"],"when":dt.isoformat()})
    return sorted(events,key=lambda x:x["when"])[:500]


@app.get("/api/weather-history")
async def weather_history(plant_id: int | None = None, irrigation_manager_session: str | None = Cookie(None)):
    u=require_user(irrigation_manager_session);ids=accessible_plant_ids(u)
    if plant_id is not None: plant_access(u,plant_id,"view_history")
    with db() as conn:
        if plant_id: rows=conn.execute("SELECT w.*,p.name plant_name FROM weather_history w JOIN plants p ON p.id=w.plant_id WHERE w.plant_id=? ORDER BY recorded_at DESC LIMIT 1000",(plant_id,)).fetchall()
        elif ids is None: rows=conn.execute("SELECT w.*,p.name plant_name FROM weather_history w JOIN plants p ON p.id=w.plant_id ORDER BY recorded_at DESC LIMIT 1000").fetchall()
        elif ids: rows=conn.execute(f"SELECT w.*,p.name plant_name FROM weather_history w JOIN plants p ON p.id=w.plant_id WHERE w.plant_id IN ({','.join('?'*len(ids))}) ORDER BY recorded_at DESC LIMIT 1000",ids).fetchall()
        else: rows=[]
    return [dict(r) for r in rows]


@app.exception_handler(HTTPException)
async def http_exception(_:Request,exc:HTTPException): return JSONResponse({"detail":exc.detail},status_code=exc.status_code)
