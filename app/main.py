"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StrictInt
from typing import Literal
from pymongo.errors import DuplicateKeyError
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True)
    
    db.employees.create_index([("department", 1), ("joined_on", 1)],name="department_joined_on_idx",)

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)]
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #



def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    overnight: bool = False,
) -> int:
    h, m = map(int, shift_start.split(":"))

    start_date = punch_in.date()
    if overnight:
        start_date -= timedelta(days=1)

    start = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        h,
        m,
        tzinfo=punch_in.tzinfo,
    )

    elapsed_seconds = (punch_in - start).total_seconds()

    if elapsed_seconds > 10 * 60:
        return int(elapsed_seconds // 60)

    return 0



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "database unavailable")




@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    doc.pop("_id", None)
    return doc




@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(422, "page must be at least 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0, "created_at": 1,
                              "emp_code": 1, "name": 1, "email": 1,
                              "department": 1, "shift_start": 1,
                              "shift_end": 1, "joined_on": 1})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for item in items:
        if item.get("created_at") is not None:
            item["created_at"] = int(
                item["created_at"].replace(tzinfo=timezone.utc).timestamp() * 1000
            )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    if body.punched_at is not None:
        if not 100000000000 <= body.punched_at <= 4102444800000:
            raise HTTPException(422, "punched_at must be valid epoch milliseconds")
        ts = datetime.fromtimestamp(body.punched_at // 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    local_ts = ts.astimezone(IST)
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]
    attendance_date = local_ts.date()

    if shift_end <= shift_start and local_ts.strftime("%H:%M") < shift_end:
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(local_ts,shift_start,overnight=(shift_end <= shift_start and local_ts.strftime("%H:%M") < shift_end),),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }




@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(422, "page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(422, "page_size must be between 1 and 100")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be <= date_to")

    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    docs = list(
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for doc in docs:
        doc["id"] = str(doc.pop("_id"))

        for field in ("punch_in", "punch_out"):
            value = doc.get(field)
            if value is not None:
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                doc[field] = int(value.timestamp() * 1000)

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None


class AttendanceCorrectionIn(BaseModel):
    status: Optional[Literal[
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str
    by: str


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    if body.punched_at is not None:
        if not 100000000000 <= body.punched_at <= 4102444800000:
            raise HTTPException(422, "punched_at must be valid epoch milliseconds")
        ts = datetime.fromtimestamp(body.punched_at // 1000, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    # Find the most recent open attendance record.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(404, "no open punch-in record found")

    punch_in_ts = record["punch_in"].replace(tzinfo=timezone.utc)

    elapsed = (ts - punch_in_ts).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(422, "punch-out must be after punch-in and within 24 hours")

    local_ts = ts.astimezone(IST)
    shift_end = emp["shift_end"]
    attendance_date = datetime.fromisoformat(record["date"]).date()

    # Overnight shifts end on the following calendar day.
    h, m = map(int, shift_end.split(":"))
    end_date = attendance_date
    if shift_end <= emp["shift_start"]:
        end_date += timedelta(days=1)

    shift_end_ts = datetime(
        end_date.year, end_date.month, end_date.day,
        h, m, tzinfo=IST,
    ).astimezone(timezone.utc)

    overtime_elapsed = (ts - shift_end_ts).total_seconds()
    overtime_minutes = (
        int(overtime_elapsed // 60)
        if overtime_elapsed >= 30 * 60
        else 0
    )

    from decimal import Decimal, ROUND_HALF_UP

    work_hours = float(
        (Decimal(str(elapsed)) / Decimal("3600")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": max(0, overtime_minutes),
                "half_day": half_day,
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(409, "attendance record already punched out")

    return {
        "emp_code": body.emp_code,
        "date": record["date"],
        "status": record["status"],
        "punch_in": int(punch_in_ts.timestamp() * 1000),
        "punch_out": int(ts.timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record["late_minutes"],
        "overtime_minutes": max(0, overtime_minutes),
        "half_day": half_day,
        "history": record.get("history", []),
    }



from datetime import date
from decimal import Decimal, ROUND_HALF_UP


@app.patch("/attendance/{emp_code}/{date}")
def correct_attendance(
    emp_code: str,
    date: str,
    body: AttendanceCorrectionIn,
):
    try:
        record_date = datetime.strptime(date, "%Y-%m-%d").date()
        if record_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if record is None:
        raise HTTPException(404, "attendance record not found")

    changes_requested = body.model_dump(exclude_unset=True)
    reason = changes_requested.pop("reason")
    by = changes_requested.pop("by")

    if not reason.strip() or not by.strip():
        raise HTTPException(422, "reason and by must not be empty")

    updates = {}

    for field, value in changes_requested.items():
        if value is not None and field == "status":
            updates[field] = value
        elif value is not None and field in ("punch_in", "punch_out"):
            if not 100000000000 <= value <= 4102444800000:
                raise HTTPException(422, f"{field} must be valid epoch milliseconds")
            updates[field] = datetime.fromtimestamp(
                value // 1000, tz=timezone.utc
            )

    new_status = updates.get("status", record["status"])
    new_in = updates.get("punch_in", record.get("punch_in"))
    new_out = updates.get("punch_out", record.get("punch_out"))

    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in updates or "punch_out" in updates:
            raise HTTPException(422, "ABSENT/LEAVE cannot have punch times")
        new_in = None
        new_out = None
        updates["punch_in"] = None
        updates["punch_out"] = None
        updates["work_hours"] = None
        updates["late_minutes"] = 0
        updates["overtime_minutes"] = 0
        updates["half_day"] = False
    else:
        if new_in is None:
            raise HTTPException(422, "presence status requires punch_in")

        local_in = new_in.replace(tzinfo=timezone.utc).astimezone(IST)
        shift_start = emp["shift_start"]
        shift_end = emp["shift_end"]

        attendance_day = local_in.date()
        overnight_early = (
            shift_end <= shift_start
            and local_in.strftime("%H:%M") < shift_end
        )
        if overnight_early:
            attendance_day -= timedelta(days=1)

        if attendance_day.isoformat() != date:
            raise HTTPException(422, "punch_in must belong to attendance date")

        updates["late_minutes"] = compute_late_minutes(
            local_in, shift_start, overnight=overnight_early
        )

        if new_out is not None:
            new_in_utc = new_in.replace(tzinfo=timezone.utc)
            new_out_utc = new_out.replace(tzinfo=timezone.utc)
            elapsed = (new_out_utc - new_in_utc).total_seconds()

            if elapsed <= 0 or elapsed > 24 * 60 * 60:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

            hours = float(
                (Decimal(str(elapsed)) / Decimal("3600")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
            updates["work_hours"] = hours
            updates["half_day"] = hours < 4.50

            end_date = record_date
            if shift_end <= shift_start:
                end_date += timedelta(days=1)

            sh, sm = map(int, shift_end.split(":"))
            shift_end_dt = datetime(
                end_date.year, end_date.month, end_date.day,
                sh, sm, tzinfo=IST
            ).astimezone(timezone.utc)

            overtime_seconds = (new_out_utc - shift_end_dt).total_seconds()
            updates["overtime_minutes"] = (
                int(overtime_seconds // 60)
                if overtime_seconds >= 30 * 60 else 0
            )
        else:
            updates["work_hours"] = None
            updates["overtime_minutes"] = 0
            updates["half_day"] = False

    actual_changes = {}
    for key, value in updates.items():
        old = record.get(key)
        if isinstance(old, datetime) and isinstance(value, datetime):
            old = old.replace(tzinfo=timezone.utc)
        if old != value:
            actual_changes[key] = value

    if not actual_changes:
        raise HTTPException(422, "patch contains no changes")

    history_changes = {}
    for key, value in actual_changes.items():
        if isinstance(value, datetime):
            history_changes[key] = int(value.timestamp() * 1000)
        else:
            history_changes[key] = value

    history_item = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": by,
        "reason": reason,
        "changes": history_changes,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": record["status"],
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {
            "$set": actual_changes,
            "$push": {"history": history_item},
        },
    )

    if result.modified_count == 0:
        raise HTTPException(409, "attendance record changed concurrently; retry")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    for item in updated.get("history", []):
        if isinstance(item.get("at"), datetime):
            at = item["at"]
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            item["at"] = int(at.timestamp() * 1000)

    return updated


from decimal import Decimal, ROUND_HALF_UP


def round_half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum, rounding=ROUND_HALF_UP
        )
    )


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    year, month_num = month_start.year, month_start.month
    if month_num == 12:
        next_month = datetime(year + 1, 1, 1).date()
    else:
        next_month = datetime(year, month_num + 1, 1).date()

    joined = datetime.strptime(emp["joined_on"], "%Y-%m-%d").date()
    effective_start = max(month_start, joined)
    effective_end = next_month

    working_days = 0
    current = effective_start
    while current < effective_end:
        if current.weekday() < 5:
            working_days += 1
        current += timedelta(days=1)

    logs = list(db.attendance_logs.find({
        "emp_code": emp_code,
        "date": {
            "$gte": month_start.isoformat(),
            "$lt": next_month.isoformat(),
        },
    }))

    present_statuses = {"PRESENT", "WFH", "ON_DUTY"}
    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for log in logs:
        day = datetime.strptime(log["date"], "%Y-%m-%d").date()

        if log["status"] == "LEAVE":
            leave_days += 1

        if log["status"] in present_statuses:
            if day.weekday() < 5:
                present_days += 0.5 if log.get("half_day") else 1.0

        late = log.get("late_minutes", 0) or 0
        if late > 0:
            late_count += 1
            total_late_minutes += late

        total_overtime_minutes += log.get("overtime_minutes", 0) or 0

    attendance_pct = (
        round_half_up(present_days / working_days, 4)
        if working_days > 0 else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    year, month_num = month_start.year, month_start.month
    if month_num == 12:
        next_month = datetime(year + 1, 1, 1).date()
    else:
        next_month = datetime(year, month_num + 1, 1).date()

    month_end = next_month.isoformat()
    month_start_str = month_start.isoformat()

    pipeline = [
        {
            "$match": {
                "joined_on": {"$lt": month_end}
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$emp_code", "$$code"]},
                            "date": {
                                "$gte": month_start_str,
                                "$lt": month_end,
                            },
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "logs": {"$push": "$logs"},
            }
        },
    ]

    results = list(db.employees.aggregate(pipeline))
    output = []

    for department in results:
        logs = department["logs"]
        # Correct headcount independently of the number of attendance logs.
        headcount = db.employees.count_documents({
            "department": department["_id"],
            "joined_on": {"$lt": month_end},
        })

        present_days = 0.0
        work_hours = []
        late_count = 0
        total_late_minutes = 0
        leave_count = 0
        on_duty_count = 0

        for log in logs:
            if not log or not log.get("emp_code"):
                continue

            day = datetime.strptime(log["date"], "%Y-%m-%d").date()
            status = log.get("status")

            if status in ("PRESENT", "WFH", "ON_DUTY") and day.weekday() < 5:
                present_days += 0.5 if log.get("half_day") else 1.0

            if status == "LEAVE":
                leave_count += 1
            if status == "ON_DUTY":
                on_duty_count += 1

            if (log.get("late_minutes") or 0) > 0:
                late_count += 1
            total_late_minutes += log.get("late_minutes", 0) or 0

            if status in ("PRESENT", "WFH", "ON_DUTY"):
                if log.get("work_hours") is not None:
                    work_hours.append(log["work_hours"])

        output.append({
            "department": department["_id"],
            "headcount": headcount,
            "present_days": present_days,
            "avg_work_hours": (
                round_half_up(sum(work_hours) / len(work_hours), 2)
                if work_hours else None
            ),
            "late_count": late_count,
            "total_late_minutes": total_late_minutes,
            "leave_count": leave_count,
            "on_duty_count": on_duty_count,
        })

    return sorted(output, key=lambda item: item["department"] or "")


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
        if month_start.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM")

    if limit < 1 or limit > 100:
        raise HTTPException(422, "limit must be between 1 and 100")

    year, month_num = month_start.year, month_start.month
    if month_num == 12:
        next_month = datetime(year + 1, 1, 1).date()
    else:
        next_month = datetime(year, month_num + 1, 1).date()

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                },
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
                "rank": 1,
            }
        },
    ])

    return list(db.attendance_logs.aggregate(pipeline))



@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: str,
    date_to: str,
):
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
        if start.isoformat() != date_from or end.isoformat() != date_to:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "dates must use YYYY-MM-DD format")

    if end < start:
        raise HTTPException(422, "date_to must be >= date_from")

    if (end - start).days + 1 > 92:
        raise HTTPException(422, "date range cannot exceed 92 days")

    if db.employees.count_documents({
        "department": department,
        "joined_on": {"$lte": date_to},
    }) == 0:
        raise HTTPException(404, "department has no employees")

    pipeline = [
        {
            "$match": {
                "date": {"$gte": date_from, "$lte": date_to}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateFromString": {"dateString": "$_id"}},
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.combine(start, datetime.min.time()),
                        datetime.combine(end, datetime.min.time()),
                    ],
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    rows = list(db.attendance_logs.aggregate(pipeline))

    # Map aggregation output by calendar date.
    by_date = {
        row["date"].date().isoformat(): row
        for row in rows
    }

    output = []
    rates = []
    current = start

    while current <= end:
        day = current.isoformat()
        working = current.weekday() < 5
        headcount = db.employees.count_documents({
            "department": department,
            "joined_on": {"$lte": day},
        })

        row = by_date.get(day, {})
        present = row.get("present_count", 0)
        late = row.get("late_count", 0)

        rate = (
            round_half_up(present / headcount, 4)
            if working and headcount > 0 else None
        )
        rates.append(rate)

        window = [value for value in rates[-7:] if value is not None]
        moving = round_half_up(sum(window) / len(window), 4) if window else None

        output.append({
            "date": day,
            "is_working_day": working,
            "headcount": headcount,
            "present_count": present,
            "late_count": late,
            "attendance_rate": rate,
            "moving_avg_7d": moving,
        })
        current += timedelta(days=1)

    return output



from typing import Literal


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        if department:
            query["department"] = department

        plan = db.attendance_logs.find(query).sort(
            [("date", -1), ("emp_code", 1)]
        ).explain()

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                422, "emp_code and month are required for employee_monthly"
            )

        try:
            start = datetime.strptime(month, "%Y-%m").date()
            if start.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")

        if start.month == 12:
            end = datetime(start.year + 1, 1, 1).date()
        else:
            end = datetime(start.year, start.month + 1, 1).date()

        plan = db.attendance_logs.find({
            "emp_code": emp_code,
            "date": {
                "$gte": start.isoformat(),
                "$lt": end.isoformat(),
            },
        }).explain()

    elif endpoint == "department_summary":
        plan = db.employees.find(
            {"department": department} if department else {}
        ).explain()

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required for late_leaderboard")

        try:
            start = datetime.strptime(month, "%Y-%m").date()
            if start.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM")

        if start.month == 12:
            end = datetime(start.year + 1, 1, 1).date()
        else:
            end = datetime(start.year, start.month + 1, 1).date()

        plan = db.attendance_logs.find({
            "date": {
                "$gte": start.isoformat(),
                "$lt": end.isoformat(),
            },
            "late_minutes": {"$gt": 0},
        }).explain()

    else:
        if not department or not date_from or not date_to:
            raise HTTPException(
                422, "department, date_from and date_to are required"
            )

        plan = db.employees.find({
            "department": department,
            "joined_on": {"$lte": date_to},
        }).explain()

    return plan


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
