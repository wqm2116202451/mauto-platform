"""项目空间与项目知识库接口。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db, fileutil
from ..deps import ApiError, current_user, ok, paginate
from .chat import conv_out, create_conversation

router = APIRouter(tags=["projects"])

MAX_FILES = 500
MAX_FILE_SIZE = 100 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg"}


class ProjectIn(BaseModel):
    name: str
    description: str = ""
    icon: str = "📁"
    color: str = "#1A5CC8"
    visibility: str = "private"
    instructions: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    visibility: str | None = None
    instructions: str | None = None
    is_archived: bool | None = None


class MemberIn(BaseModel):
    email: str
    role: str = "viewer"


class MemberPatch(BaseModel):
    role: str


class FilePatch(BaseModel):
    file_name: str | None = None
    folder: str | None = None


def _member(project_id: str, user_id: str) -> dict | None:
    return db.q1("SELECT * FROM project_member WHERE project_id = ? AND user_id = ?", (project_id, user_id))


def project_role(project: dict, user: dict) -> str:
    if project["owner_id"] == user["id"]:
        return "owner"
    m = _member(project["id"], user["id"])
    return m["role"] if m else ""


def get_project(project_id: str, user: dict, write: bool = False, allow_archived: bool = True) -> dict:
    row = db.q1("SELECT * FROM project WHERE id = ? AND is_deleted = 0", (project_id,))
    if not row:
        raise ApiError("项目不存在或已删除", code=404, status_code=404)
    role = project_role(row, user)
    if not role:
        raise ApiError("无权访问该项目", code=403, status_code=403)
    if write and role == "viewer":
        raise ApiError("当前角色为查看者，无编辑权限", code=403, status_code=403)
    if row["is_archived"] and not allow_archived:
        raise ApiError("项目已归档", code=400)
    return {**row, "role": role}


def _counts(project_id: str) -> tuple[int, int]:
    files = db.q1("SELECT COUNT(*) AS n FROM project_file WHERE project_id = ?", (project_id,))["n"]
    convs = db.q1("SELECT COUNT(*) AS n FROM conversation WHERE project_id = ?", (project_id,))["n"]
    return files, convs


def project_out(row: dict, user: dict) -> dict:
    files, convs = _counts(row["id"])
    members = db.q(
        "SELECT pm.id, pm.role, u.id AS user_id, u.nickname, u.email, u.avatar "
        "FROM project_member pm JOIN user u ON u.id = pm.user_id WHERE pm.project_id = ?", (row["id"],)
    )
    owner = db.q1("SELECT id, nickname, email FROM user WHERE id = ?", (row["owner_id"],))
    return {
        "id": row["id"], "name": row["name"], "description": row["description"],
        "icon": row["icon"], "color": row["color"], "visibility": row["visibility"],
        "instructions": row["instructions"], "is_archived": bool(row["is_archived"]),
        "file_count": files, "conversation_count": convs,
        "owner": owner, "members": members, "role": project_role(row, user),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _file_out(row: dict) -> dict:
    return {
        "id": row["id"], "file_name": row["file_name"], "folder": row["folder"],
        "file_size": row["file_size"], "size_text": fileutil.human_size(row["file_size"]),
        "mime": row["mime"], "parse_status": row["parse_status"],
        "text_len": row["text_len"], "created_at": row["created_at"],
        "excerpt": (row.get("text_excerpt") or "")[:300],
    }


@router.get("/projects")
def list_projects(archived: int = 0, q: str = "", sort: str = "recent",
                  page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=60),
                  user: dict = Depends(current_user)):
    ids = [r["project_id"] for r in db.q(
        "SELECT project_id FROM project_member WHERE user_id = ?", (user["id"],))]
    ids.append("__owner__" + user["id"])
    where = ["is_deleted = 0", "is_archived = ?",
             "(owner_id = ? OR id IN (SELECT project_id FROM project_member WHERE user_id = ?))"]
    args: list = [1 if archived else 0, user["id"], user["id"]]
    if q.strip():
        where.append("(name LIKE ? OR description LIKE ?)")
        args += [f"%{q.strip()}%", f"%{q.strip()}%"]
    cond = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS n FROM project WHERE {cond}", args)["n"]
    order = "created_at DESC" if sort == "created" else "updated_at DESC"
    rows = db.q(f"SELECT * FROM project WHERE {cond} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size])
    return ok(paginate([project_out(r, user) for r in rows], page, page_size, total))


@router.post("/projects")
def create_project(payload: ProjectIn, user: dict = Depends(current_user)):
    name = payload.name.strip()
    if not name:
        raise ApiError("请输入项目名称")
    if len(name) > 50:
        raise ApiError("项目名称不能超过 50 字")
    if len(payload.description) > 200:
        raise ApiError("项目描述不能超过 200 字")
    if payload.visibility not in ("private", "team"):
        raise ApiError("可见范围不合法")
    now = db.now_utc()
    pid = db.uid("prj_")
    db.insert("project", {
        "id": pid, "owner_id": user["id"], "name": name,
        "description": payload.description.strip(), "icon": payload.icon or "📁",
        "color": payload.color, "visibility": payload.visibility,
        "instructions": payload.instructions, "is_archived": 0, "is_deleted": 0,
        "deleted_at": None, "created_at": now, "updated_at": now,
    })
    db.insert("project_member", {"id": db.uid("pm_"), "project_id": pid, "user_id": user["id"],
                                 "role": "owner", "created_at": now})
    return ok(project_out(db.q1("SELECT * FROM project WHERE id = ?", (pid,)), user))


@router.get("/projects/trash")
def trash(user: dict = Depends(current_user)):
    rows = db.q("SELECT * FROM project WHERE owner_id = ? AND is_deleted = 1 ORDER BY deleted_at DESC",
                (user["id"],))
    return ok([{"id": r["id"], "name": r["name"], "icon": r["icon"],
                "deleted_at": r["deleted_at"], "days_left": 30} for r in rows])


@router.get("/projects/{project_id}")
def project_detail(project_id: str, user: dict = Depends(current_user)):
    project = get_project(project_id, user)
    files = db.q("SELECT * FROM project_file WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
    convs = db.q(
        "SELECT * FROM conversation WHERE project_id = ? AND is_archived = 0 ORDER BY updated_at DESC",
        (project_id,),
    )
    folders = sorted({f["folder"] for f in files})
    return ok({
        **project_out(project, user),
        "files": [_file_out(f) for f in files],
        "folders": folders or ["根目录"],
        "conversations": [conv_out(c) for c in convs],
    })


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, payload: ProjectPatch, user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    data: dict = {"updated_at": db.now_utc()}
    if payload.name is not None:
        name = payload.name.strip()
        if not name or len(name) > 50:
            raise ApiError("项目名称需为 1~50 字")
        data["name"] = name
    if payload.description is not None:
        if len(payload.description) > 200:
            raise ApiError("项目描述不能超过 200 字")
        data["description"] = payload.description
    for field in ("icon", "color", "visibility", "instructions"):
        value = getattr(payload, field)
        if value is not None:
            data[field] = value
    if payload.is_archived is not None:
        data["is_archived"] = 1 if payload.is_archived else 0
    db.update("project", project["id"], data)
    return ok(project_out(db.q1("SELECT * FROM project WHERE id = ?", (project_id,)), user))


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, confirm_name: str = "", user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    if project["owner_id"] != user["id"]:
        raise ApiError("仅拥有者可以删除项目", code=403, status_code=403)
    if confirm_name.strip() != project["name"]:
        raise ApiError("请输入完整项目名称以确认删除")
    db.update("project", project_id, {"is_deleted": 1, "deleted_at": db.now_utc(),
                                      "is_archived": 1})
    return ok({"deleted": True, "restore_days": 30})


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM project WHERE id = ? AND owner_id = ? AND is_deleted = 1",
                (project_id, user["id"]))
    if not row:
        raise ApiError("回收站中没有该项目", code=404, status_code=404)
    db.update("project", project_id, {"is_deleted": 0, "is_archived": 0, "deleted_at": None,
                                      "updated_at": db.now_utc()})
    return ok({"restored": True})


# ------------------------------------------------------------------ 文件
@router.get("/projects/{project_id}/files")
def list_files(project_id: str, folder: str = "", user: dict = Depends(current_user)):
    get_project(project_id, user)
    where, args = ["project_id = ?"], [project_id]
    if folder:
        where.append("folder = ?")
        args.append(folder)
    rows = db.q(f"SELECT * FROM project_file WHERE {' AND '.join(where)} ORDER BY created_at DESC", args)
    return ok([_file_out(r) for r in rows])


@router.post("/projects/{project_id}/files")
def upload_project_file(project_id: str, file: UploadFile = File(...), folder: str = Form("根目录"),
                        user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    name = Path(file.filename or "file").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise ApiError(f"不支持的文件类型 {ext}")
    count = db.q1("SELECT COUNT(*) AS n FROM project_file WHERE project_id = ?", (project_id,))["n"]
    if count >= MAX_FILES:
        raise ApiError("单项目文件数量已达上限（500 个）")
    raw = file.file.read()
    if len(raw) > MAX_FILE_SIZE:
        raise ApiError("单个文件不能超过 100MB")
    fid = db.uid("pf_")
    stored = Path(db.UPLOAD_DIR) / f"{fid}{ext}"
    stored.write_bytes(raw)
    status, text = fileutil.extract_text(stored, name)
    db.insert("project_file", {
        "id": fid, "project_id": project_id, "folder": folder or "根目录", "file_name": name,
        "stored_path": str(stored), "file_size": len(raw), "mime": file.content_type or "",
        "parse_status": status, "text_len": len(text),
        "text_excerpt": text[:20000] if status == "parsed" else "", "created_at": db.now_utc(),
    })
    db.update("project", project_id, {"updated_at": db.now_utc()})
    return ok(_file_out(db.q1("SELECT * FROM project_file WHERE id = ?", (fid,))))


@router.patch("/projects/{project_id}/files/{file_id}")
def patch_file(project_id: str, file_id: str, payload: FilePatch, user: dict = Depends(current_user)):
    get_project(project_id, user, write=True)
    row = db.q1("SELECT * FROM project_file WHERE id = ? AND project_id = ?", (file_id, project_id))
    if not row:
        raise ApiError("文件不存在", code=404, status_code=404)
    data = {}
    if payload.file_name:
        data["file_name"] = Path(payload.file_name).name
    if payload.folder:
        data["folder"] = payload.folder
    if data:
        db.update("project_file", file_id, data)
    return ok(_file_out(db.q1("SELECT * FROM project_file WHERE id = ?", (file_id,))))


@router.post("/projects/{project_id}/files/{file_id}/reparse")
def reparse_file(project_id: str, file_id: str, user: dict = Depends(current_user)):
    get_project(project_id, user, write=True)
    row = db.q1("SELECT * FROM project_file WHERE id = ? AND project_id = ?", (file_id, project_id))
    if not row:
        raise ApiError("文件不存在", code=404, status_code=404)
    status, text = fileutil.extract_text(Path(row["stored_path"]), row["file_name"])
    db.ex("UPDATE project_file SET parse_status = ?, text_len = ?, text_excerpt = ? WHERE id = ?",
          (status, len(text), text[:20000], file_id))
    return ok(_file_out(db.q1("SELECT * FROM project_file WHERE id = ?", (file_id,))))


@router.delete("/projects/{project_id}/files/{file_id}")
def delete_file(project_id: str, file_id: str, user: dict = Depends(current_user)):
    get_project(project_id, user, write=True)
    db.ex("DELETE FROM project_file WHERE id = ? AND project_id = ?", (file_id, project_id))
    return ok({"deleted": True})


@router.get("/project-files/{file_id}")
def download_project_file(file_id: str, user: dict = Depends(current_user)):
    row = db.q1("SELECT * FROM project_file WHERE id = ?", (file_id,))
    if not row:
        raise ApiError("文件不存在", code=404, status_code=404)
    project = db.q1("SELECT * FROM project WHERE id = ?", (row["project_id"],))
    if not project or not project_role(project, user):
        raise ApiError("无权访问该文件", code=403, status_code=403)
    return FileResponse(row["stored_path"], filename=row["file_name"])


# ------------------------------------------------------------------ 成员
@router.get("/projects/{project_id}/members")
def list_members(project_id: str, user: dict = Depends(current_user)):
    get_project(project_id, user)
    rows = db.q(
        "SELECT pm.id, pm.role, pm.created_at, u.id AS user_id, u.nickname, u.email "
        "FROM project_member pm JOIN user u ON u.id = pm.user_id WHERE pm.project_id = ?",
        (project_id,),
    )
    return ok(rows)


@router.post("/projects/{project_id}/members")
def add_member(project_id: str, payload: MemberIn, user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    if project["owner_id"] != user["id"]:
        raise ApiError("仅拥有者可以邀请成员", code=403, status_code=403)
    if payload.role not in ("editor", "viewer"):
        raise ApiError("角色仅支持编辑者或查看者")
    target = db.q1("SELECT * FROM user WHERE email = ?", (payload.email.strip().lower(),))
    if not target:
        raise ApiError("该邮箱尚未注册 MAuto 账号")
    if target["id"] == project["owner_id"]:
        raise ApiError("拥有者已在项目中")
    existing = _member(project_id, target["id"])
    if existing:
        db.ex("UPDATE project_member SET role = ? WHERE id = ?", (payload.role, existing["id"]))
    else:
        db.insert("project_member", {"id": db.uid("pm_"), "project_id": project_id,
                                     "user_id": target["id"], "role": payload.role,
                                     "created_at": db.now_utc()})
    return ok({"added": True, "user": {"id": target["id"], "nickname": target["nickname"],
                                       "email": target["email"], "role": payload.role}})


@router.patch("/projects/{project_id}/members/{member_id}")
def patch_member(project_id: str, member_id: str, payload: MemberPatch, user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    if project["owner_id"] != user["id"]:
        raise ApiError("仅拥有者可以修改成员权限", code=403, status_code=403)
    row = db.q1("SELECT * FROM project_member WHERE id = ? AND project_id = ?", (member_id, project_id))
    if not row or row["role"] == "owner":
        raise ApiError("成员不存在或不可修改")
    db.ex("UPDATE project_member SET role = ? WHERE id = ?", (payload.role, member_id))
    return ok({"updated": True})


@router.delete("/projects/{project_id}/members/{member_id}")
def remove_member(project_id: str, member_id: str, user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    if project["owner_id"] != user["id"]:
        raise ApiError("仅拥有者可以移除成员", code=403, status_code=403)
    db.ex("DELETE FROM project_member WHERE id = ? AND project_id = ? AND role != 'owner'",
          (member_id, project_id))
    return ok({"removed": True})


# ------------------------------------------------------------------ 项目会话
@router.get("/projects/{project_id}/conversations")
def project_conversations(project_id: str, user: dict = Depends(current_user)):
    get_project(project_id, user)
    rows = db.q("SELECT * FROM conversation WHERE project_id = ? ORDER BY updated_at DESC", (project_id,))
    return ok([conv_out(r) for r in rows])


@router.post("/projects/{project_id}/conversations")
def new_project_conversation(project_id: str, user: dict = Depends(current_user)):
    project = get_project(project_id, user, write=True)
    conv = create_conversation(user["id"], f"{project['name']} · 新会话", project_id)
    return ok(conv_out(conv))
