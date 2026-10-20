"""组织架构与员工管理（PRD 4.1）。

覆盖：F-ORG-01 树展示/拖拽归属、F-ORG-02 新增（含头像）、F-ORG-03/04 岗位职责与性格、
F-ORG-05 性别、F-ORG-06 员工级 LLM 绑定、F-ORG-07 ASR/TTS 覆盖、F-ORG-08 启用停用。
"""
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..db import AVATAR_DIR, execute, query, query_one

router = APIRouter(prefix="/api", tags=["org"])


@router.get("/departments")
def list_departments():
    depts = query("SELECT * FROM departments ORDER BY sort_order, id")
    emps = query("SELECT id, dept_id, name, gender, enabled, avatar_path FROM employees ORDER BY id")
    for d in depts:
        d["employees"] = [e for e in emps if e["dept_id"] == d["id"]]
    unassigned = [e for e in emps if e["dept_id"] is None]
    return {"departments": depts, "unassigned": unassigned}


@router.post("/departments")
def create_department(body: dict | None = None):
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


@router.put("/employees/{eid}/dept")
def move_employee(eid: int, body: dict):
    """F-ORG-01 拖拽调整部门归属。"""
    row = query_one("SELECT * FROM employees WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "员工不存在")
    dept_id = body.get("dept_id")
    execute("UPDATE employees SET dept_id=? WHERE id=?", (dept_id, eid))
    return {"ok": True, "dept_id": dept_id}


@router.get("/employees")
def list_employees():
    rows = query("SELECT * FROM employees ORDER BY id")
    for r in rows:
        r["avatar_url"] = f"/api/employees/{r['id']}/avatar" if r.get("avatar_path") else None
    return rows


def _emp_body_check(body: dict | None = None):
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "姓名必填")
    if not (body.get("role_prompt") or "").strip():
        raise HTTPException(400, "岗位职责必填")
    if body.get("gender") not in ("男", "女", "其他"):
        raise HTTPException(400, "性别必须是 男/女/其他")


@router.post("/employees")
def create_employee(body: dict | None = None):
    _emp_body_check(body)
    eid = execute(
        "INSERT INTO employees(dept_id, name, gender, role_prompt, personality_prompt, "
        "llm_config_id, asr_config_id, tts_config_id, tts_voice_id, enabled) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (body.get("dept_id"), body["name"].strip(), body.get("gender", "其他"),
         body.get("role_prompt", ""), body.get("personality_prompt", ""),
         body.get("llm_config_id"), body.get("asr_config_id"),
         body.get("tts_config_id"), body.get("tts_voice_id"),
         1 if body.get("enabled", 1) else 0))
    return query_one("SELECT * FROM employees WHERE id=?", (eid,))


@router.put("/employees/{eid}")
def update_employee(eid: int, body: dict):
    row = query_one("SELECT * FROM employees WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "员工不存在")
    merged = {**row, **{k: v for k, v in body.items() if k in body}}
    _emp_body_check(merged)
    cols = ["dept_id", "name", "gender", "role_prompt", "personality_prompt", "llm_config_id",
            "asr_config_id", "tts_config_id", "tts_voice_id", "enabled"]
    vals = [body[c] if c in body else row[c] for c in cols]
    execute(f"UPDATE employees SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*vals, eid))
    return query_one("SELECT * FROM employees WHERE id=?", (eid,))


@router.delete("/employees/{eid}")
def delete_employee(eid: int):
    execute("DELETE FROM employees WHERE id=?", (eid,))
    return {"ok": True}


@router.post("/employees/{eid}/avatar")
def upload_avatar(eid: int, file: UploadFile = File(...)):
    """F-ORG-02：头像上传，复制进应用数据目录。"""
    row = query_one("SELECT * FROM employees WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "员工不存在")
    ext = Path(file.filename or "avatar.png").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        ext = ".png"
    name = f"emp_{eid}_{uuid.uuid4().hex[:8]}{ext}"
    dest = AVATAR_DIR / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    if row.get("avatar_path"):
        old = Path(row["avatar_path"])
        if old.exists() and str(old).startswith(str(AVATAR_DIR)):
            old.unlink()
    execute("UPDATE employees SET avatar_path=? WHERE id=?", (str(dest), eid))
    return {"ok": True, "avatar_path": str(dest)}


@router.get("/employees/{eid}/avatar")
def get_avatar(eid: int):
    from fastapi.responses import FileResponse

    row = query_one("SELECT avatar_path FROM employees WHERE id=?", (eid,))
    if not row or not row.get("avatar_path"):
        raise HTTPException(404, "未设置头像")
    p = Path(row["avatar_path"])
    if not p.exists():
        raise HTTPException(404, "头像文件缺失")
    return FileResponse(p)


@router.delete("/employees/{eid}/avatar")
def delete_avatar(eid: int):
    row = query_one("SELECT avatar_path FROM employees WHERE id=?", (eid,))
    if row and row.get("avatar_path"):
        p = Path(row["avatar_path"])
        if p.exists() and str(p).startswith(str(AVATAR_DIR)):
            p.unlink()
    execute("UPDATE employees SET avatar_path=NULL WHERE id=?", (eid,))
    return {"ok": True}


@router.get("/employees/{eid}/prompt-preview")
def prompt_preview(eid: int, topic: str = "示例议题"):
    """实时预览 system prompt 拼接结果（交互要求）。"""
    from ..orchestrator import _system_prompt

    row = query_one("SELECT * FROM employees WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "员工不存在")
    return {"system_prompt": _system_prompt(row, topic)}
