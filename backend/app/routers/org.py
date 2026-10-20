"""组织架构与员工管理（PRD 4.1）。"""
from fastapi import APIRouter, HTTPException

from ..db import execute, query, query_one

router = APIRouter(prefix="/api", tags=["org"])


@router.get("/departments")
def list_departments():
    depts = query("SELECT * FROM departments ORDER BY sort_order, id")
    emps = query("SELECT id, dept_id, name, gender, enabled FROM employees ORDER BY id")
    for d in depts:
        d["employees"] = [e for e in emps if e["dept_id"] == d["id"]]
    unassigned = [e for e in emps if e["dept_id"] is None]
    return {"departments": depts, "unassigned": unassigned}


@router.post("/departments")
def create_department(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "部门名称必填")
    did = execute("INSERT INTO departments(name, parent_id, sort_order) VALUES (?,?,?)",
                  (name, body.get("parent_id"), body.get("sort_order", 0)))
    return query_one("SELECT * FROM departments WHERE id=?", (did,))


@router.put("/departments/{did}")
def update_department(did: int, body: dict):
    row = query_one("SELECT * FROM departments WHERE id=?", (did,))
    if not row:
        raise HTTPException(404, "部门不存在")
    execute("UPDATE departments SET name=?, parent_id=?, sort_order=? WHERE id=?",
            (body.get("name", row["name"]), body.get("parent_id", row["parent_id"]),
             body.get("sort_order", row["sort_order"]), did))
    return {"ok": True}


@router.delete("/departments/{did}")
def delete_department(did: int):
    execute("DELETE FROM departments WHERE id=?", (did,))
    return {"ok": True}


@router.get("/employees")
def list_employees():
    return query("SELECT * FROM employees ORDER BY id")


def _emp_body_check(body: dict):
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "姓名必填")
    if not (body.get("role_prompt") or "").strip():
        raise HTTPException(400, "岗位职责必填")


@router.post("/employees")
def create_employee(body: dict):
    _emp_body_check(body)
    eid = execute(
        "INSERT INTO employees(dept_id, name, gender, role_prompt, personality_prompt, "
        "llm_config_id, asr_config_id, tts_config_id, tts_voice_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (body.get("dept_id"), body["name"].strip(), body.get("gender", "其他"),
         body.get("role_prompt", ""), body.get("personality_prompt", ""),
         body.get("llm_config_id"), body.get("asr_config_id"),
         body.get("tts_config_id"), body.get("tts_voice_id")))
    return query_one("SELECT * FROM employees WHERE id=?", (eid,))


@router.put("/employees/{eid}")
def update_employee(eid: int, body: dict):
    row = query_one("SELECT * FROM employees WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "员工不存在")
    merged = {**row, **{k: v for k, v in body.items() if v is not None or k in body}}
    _emp_body_check(merged)
    cols = ["dept_id", "name", "gender", "role_prompt", "personality_prompt", "llm_config_id",
            "asr_config_id", "tts_config_id", "tts_voice_id", "enabled"]
    vals = []
    for c in cols:
        vals.append(body[c] if c in body else row[c])
    execute(f"UPDATE employees SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*vals, eid))
    return query_one("SELECT * FROM employees WHERE id=?", (eid,))


@router.delete("/employees/{eid}")
def delete_employee(eid: int):
    execute("DELETE FROM employees WHERE id=?", (eid,))
    return {"ok": True}
