"""Catalogue of the (simulated) campus: buildings, rooms and their electrical loads.

Loads are in watts and are rough but realistic figures for an Indian college:
LED tube lights, split ACs / fans, projectors, lab PCs and workshop machines.
Edit this file to model your own campus - everything else adapts.
"""

from __future__ import annotations

from datetime import datetime

CAMPUS_SCOPE = "CAMPUS"

# open/close are campus-local hours; days uses Python weekday numbers (Mon=0).
BUILDINGS: dict[str, dict] = {
    "AB1": {"name": "Academic Block 1", "open": 7, "close": 19, "days": [0, 1, 2, 3, 4, 5]},
    "LAB": {"name": "Laboratory Complex", "open": 8, "close": 18, "days": [0, 1, 2, 3, 4, 5]},
    "LIB": {"name": "Central Library", "open": 8, "close": 22, "days": [0, 1, 2, 3, 4, 5, 6]},
    "ADM": {"name": "Admin & Auditorium", "open": 8, "close": 19, "days": [0, 1, 2, 3, 4, 5]},
    "HST": {"name": "Hostel Commons", "open": 0, "close": 24, "days": [0, 1, 2, 3, 4, 5, 6]},
}

# type -> default loads (W) and capacity (people)
ROOM_TYPES: dict[str, dict] = {
    "classroom":       {"lights_w": 480,  "hvac_w": 3000,  "equipment_w": 250,  "standby_w": 40,  "capacity": 60},
    "smart_classroom": {"lights_w": 600,  "hvac_w": 3500,  "equipment_w": 600,  "standby_w": 80,  "capacity": 70},
    "tutorial":        {"lights_w": 240,  "hvac_w": 1500,  "equipment_w": 150,  "standby_w": 30,  "capacity": 30},
    "computer_lab":    {"lights_w": 900,  "hvac_w": 7000,  "equipment_w": 6600, "standby_w": 400, "capacity": 60},
    "electronics_lab": {"lights_w": 800,  "hvac_w": 4500,  "equipment_w": 3000, "standby_w": 150, "capacity": 40},
    "workshop":        {"lights_w": 1200, "hvac_w": 1500,  "equipment_w": 9000, "standby_w": 200, "capacity": 40},
    "reading_hall":    {"lights_w": 2000, "hvac_w": 9000,  "equipment_w": 300,  "standby_w": 150, "capacity": 200},
    "digital_library": {"lights_w": 700,  "hvac_w": 4000,  "equipment_w": 3000, "standby_w": 250, "capacity": 40},
    "office":          {"lights_w": 600,  "hvac_w": 3000,  "equipment_w": 1200, "standby_w": 200, "capacity": 25},
    "auditorium":      {"lights_w": 4000, "hvac_w": 15000, "equipment_w": 3000, "standby_w": 300, "capacity": 500},
    "seminar_hall":    {"lights_w": 1200, "hvac_w": 5000,  "equipment_w": 800,  "standby_w": 100, "capacity": 120},
    "common_room":     {"lights_w": 600,  "hvac_w": 1200,  "equipment_w": 800,  "standby_w": 300, "capacity": 80},
    "study_room":      {"lights_w": 500,  "hvac_w": 2000,  "equipment_w": 200,  "standby_w": 50,  "capacity": 40},
}

_ROOM_LIST = [
    ("AB1-101", "AB1", "Classroom 101", "classroom"),
    ("AB1-102", "AB1", "Classroom 102", "classroom"),
    ("AB1-103", "AB1", "Classroom 103", "classroom"),
    ("AB1-201", "AB1", "Smart Classroom 201", "smart_classroom"),
    ("AB1-202", "AB1", "Smart Classroom 202", "smart_classroom"),
    ("AB1-301", "AB1", "Tutorial Room 301", "tutorial"),
    ("LAB-CS1", "LAB", "Computer Lab 1", "computer_lab"),
    ("LAB-CS2", "LAB", "Computer Lab 2", "computer_lab"),
    ("LAB-ECE", "LAB", "Electronics Lab", "electronics_lab"),
    ("LAB-MECH", "LAB", "Mechanical Workshop", "workshop"),
    ("LIB-RH", "LIB", "Reading Hall", "reading_hall"),
    ("LIB-DL", "LIB", "Digital Library", "digital_library"),
    ("ADM-OFF", "ADM", "Admin Office", "office"),
    ("ADM-AUD", "ADM", "Main Auditorium", "auditorium"),
    ("ADM-SEM", "ADM", "Seminar Hall", "seminar_hall"),
    ("HST-CR", "HST", "Hostel Common Room", "common_room"),
    ("HST-SR", "HST", "Hostel Study Room", "study_room"),
]


def _make_room(room_id: str, building_id: str, name: str, room_type: str) -> dict:
    room = {"room_id": room_id, "building_id": building_id, "name": name, "type": room_type}
    room.update(ROOM_TYPES[room_type])
    room["rated_w"] = room["lights_w"] + room["hvac_w"] + room["equipment_w"] + room["standby_w"]
    # Anything above this while nobody is in the room counts as waste.
    room["idle_threshold_w"] = round(room["standby_w"] * 1.5 + 150)
    return room


ROOMS: dict[str, dict] = {r[0]: _make_room(*r) for r in _ROOM_LIST}


def get_room(room_id: str, building_id: str | None = None) -> dict:
    """Return the room profile. Unknown rooms (e.g. a new ESP32 node) get a generic profile."""
    if room_id in ROOMS:
        return ROOMS[room_id]
    room = _make_room(room_id, "IOT", room_id, "classroom")
    room["building_id"] = building_id if building_id else "IOT"
    room["custom"] = True
    return room


def building_name(building_id: str) -> str:
    return BUILDINGS.get(building_id, {}).get("name", building_id)


def is_open(building_id: str, dt: datetime) -> bool:
    b = BUILDINGS.get(building_id)
    if b is None:
        return True  # unknown building: never raise after-hours alerts
    hour = dt.hour + dt.minute / 60
    return dt.weekday() in b["days"] and b["open"] <= hour < b["close"]


def scopes() -> list[str]:
    """Aggregation scopes used for hourly totals and forecasts."""
    return list(BUILDINGS) + [CAMPUS_SCOPE]


def describe() -> dict:
    """Campus layout for the dashboard."""
    return {
        "buildings": [
            {"id": bid, "name": b["name"], "open": b["open"], "close": b["close"], "days": b["days"],
             "rooms": [r["room_id"] for r in ROOMS.values() if r["building_id"] == bid]}
            for bid, b in BUILDINGS.items()
        ],
        "rooms": [
            {k: r[k] for k in ("room_id", "building_id", "name", "type", "capacity", "rated_w", "idle_threshold_w")}
            for r in ROOMS.values()
        ],
    }
