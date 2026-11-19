from fastapi import FastAPI, Body, Depends, HTTPException
from pydantic import BaseModel
import os
import sqlite3
import time
from typing import Optional, List

from . import rag as rag_mod
from .auth import (
    get_current_user,
    require_admin,
    hash_password,
    verify_password,
    create_access_token,
    ROLE_ADMIN,
    ROLE_USER,
)
from .rag import answer as rag_answer, get_kb

app = FastAPI(title="酒店房态与入住协同MVP")
DB_FILE = "hotel.db"

# 初始化数据库表
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS room (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_no TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL,
        remark TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS workorder (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER,
        wtype TEXT,
        status TEXT,
        creator TEXT,
        result TEXT,
        FOREIGN KEY(room_id) REFERENCES room(id)
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        role TEXT,
        password_hash TEXT
    )
    ''')
    # 兼容旧库：缺列时补 password_hash
    cols = [r[1] for r in cur.execute("PRAGMA table_info(user)").fetchall()]
    if "password_hash" not in cols:
        cur.execute("ALTER TABLE user ADD COLUMN password_hash TEXT")
    cur.execute('''
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator TEXT,
        content TEXT,
        create_time REAL
    )
    ''')
    # 模拟初始数据
    cur.execute("INSERT OR IGNORE INTO room(room_no,status,remark) VALUES ('302','清扫完成，未质检','')")
    cur.execute("INSERT OR IGNORE INTO room(room_no,status,remark) VALUES ('303','可入住','')")
    cur.execute("INSERT OR IGNORE INTO room(room_no,status,remark) VALUES ('304','维修中','水管故障')")
    # 演示账号：admin/admin123（管理员）、user/user123（普通用户）
    cur.execute(
        "INSERT OR IGNORE INTO user(username,role,password_hash) VALUES (?,?,?)",
        ("admin", ROLE_ADMIN, hash_password("admin123")),
    )
    cur.execute(
        "INSERT OR IGNORE INTO user(username,role,password_hash) VALUES (?,?,?)",
        ("user", ROLE_USER, hash_password("user123")),
    )
    conn.commit()
    conn.close()

init_db()

# 模型
class RoomStatusUpdate(BaseModel):
    status: str
    remark: Optional[str] = ""

class WorkOrderCreate(BaseModel):
    room_id: int
    wtype: str
    creator: str
    result: Optional[str] = ""

class LoginRequest(BaseModel):
    username: str
    password: str

# 认证：查库校验密码，返回 JWT 所需的用户名与角色
def authenticate_user(username: str, password: str):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT username, role, password_hash FROM user WHERE username=?", (username,)
    ).fetchone()
    conn.close()
    if not row or not row[2] or not verify_password(password, row[2]):
        return None
    # 旧数据里的中文角色统一映射为英文角色
    role = ROLE_ADMIN if row[1] in (ROLE_ADMIN, "管理员") else ROLE_USER
    return {"username": row[0], "role": role}

# 日志记录
def add_log(operator:str, content:str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO audit_log(operator,content,create_time) VALUES (?,?,?)",(operator,content,time.time()))
    conn.commit()
    conn.close()

# 接口
@app.post("/login")
def login(cred: LoginRequest):
    """登录换取 JWT：管理员 admin/admin123，普通用户 user/user123。"""
    user = authenticate_user(cred.username, cred.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user["username"], user["role"])
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}

@app.get("/rooms")
def get_all_rooms(role: str = Depends(get_current_user)):
    conn = sqlite3.connect(DB_FILE)
    res = conn.execute("SELECT * FROM room").fetchall()
    conn.close()
    return {"rooms": res, "role": role}

@app.get("/room/{room_id}")
def get_room(room_id:int, role: str = Depends(get_current_user)):
    conn = sqlite3.connect(DB_FILE)
    r = conn.execute("SELECT * FROM room WHERE id=?",(room_id,)).fetchone()
    conn.close()
    return {"room": r}

@app.post("/workorder")
def create_workorder(wo: WorkOrderCreate, operator: str = Depends(require_admin)):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO workorder(room_id,wtype,status,creator,result) VALUES (?,?,?,?,?)",
                (wo.room_id, wo.wtype, "新建", wo.creator, wo.result))
    conn.commit()
    wid = cur.lastrowid
    add_log(operator, f"管理员{operator}创建工单 {wid}（发起人 {wo.creator}）")
    conn.close()
    return {"workorder_id":wid}

@app.put("/room/{room_id}/status")
def update_room_status(room_id:int, data: RoomStatusUpdate, operator: str = Depends(require_admin)):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE room SET status=?,remark=? WHERE id=?",(data.status,data.remark,room_id))
    conn.commit()
    add_log(operator, f"房间{room_id}状态更新为 {data.status}")
    conn.close()
    return {"msg":"更新成功"}

@app.get("/logs")
def get_logs(role: str = Depends(require_admin)):
    conn = sqlite3.connect(DB_FILE)
    res = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    return {"logs":res}

# ---------- 健康检查（公网部署探活） ----------
@app.get("/health")
def health():
    """无鉴权探活接口，供部署平台/负载均衡检查服务存活性。"""
    return {"status": "ok", "service": "hotel-room-checkin", "ts": time.time()}

# ---------- 房态校验闭环（MVP 核心业务链路） ----------
def _can_checkin(status: str):
    """依据知识库房态规则判断能否入住，返回 (是否可入住, 判定原因)。"""
    s = status or ""
    if "维修" in s:
        return False, "维修中，不可入住，需工程处理"
    if "可入住" in s or ("清扫完成" in s and "质检通过" in s):
        return True, "清扫完成且质检通过，可入住"
    if "清扫完成" in s and "未质检" in s:
        return False, "仅清扫未质检，需质检复查"
    if "质检不通过" in s:
        return False, "质检不通过，需重新清扫并复查"
    return False, "房态不满足入住条件，需人工核查"

@app.post("/room/{room_id}/checkin")
def checkin_check(room_id:int, operator: str = Depends(get_current_user)):
    """校验房态能否入住（只给建议，不自动办理入住/开锁）；不合格自动生成质检复查工单。"""
    conn = sqlite3.connect(DB_FILE)
    r = conn.execute("SELECT id,room_no,status,remark FROM room WHERE id=?",(room_id,)).fetchone()
    conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="房间不存在")
    rid, room_no, status, remark = r
    ok, reason = _can_checkin(status)
    workorder_id = None
    if not ok:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("INSERT INTO workorder(room_id,wtype,status,creator,result) VALUES (?,?,?,?,?)",
                    (rid, "质检复查", "新建", operator, reason))
        conn.commit()
        workorder_id = cur.lastrowid
        conn.close()
        add_log(operator, f"房间{room_no}入住校验不通过（{reason}），自动生成质检复查工单 {workorder_id}")
    else:
        add_log(operator, f"房间{room_no}入住校验通过")
    return {
        "room_id": rid,
        "room_no": room_no,
        "raw_status": status,
        "can_checkin": ok,
        "reason": reason,
        "auto_workorder_id": workorder_id,
        "action": "仅提供建议，实际入住/开锁须人工确认",
    }

# ---------- 知识库 / RAG / LLM Wiki ----------
from pydantic import Field

class Question(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=10)

@app.get("/knowledge/reload")
def knowledge_reload(role: str = Depends(require_admin)):
    """重新加载知识库索引（管理员）。"""
    kb = rag_mod.KnowledgeBase()
    rag_mod._KB = kb
    return {"msg": "知识库已重新加载", "chunks": len(kb.chunks)}

@app.post("/rag/ask")
def rag_ask(q: Question, role: str = Depends(get_current_user)):
    """RAG 问答：无密钥时本地抽取式作答，答案携带来源。"""
    return rag_answer(q.question, top_k=q.top_k)

@app.post("/rag/wiki")
def rag_wiki_endpoint(q: Question, role: str = Depends(get_current_user)):
    """LLM Wiki：把知识片段整理成结构化词条。"""
    return rag_mod.wiki(q.question)

@app.get("/knowledge/stats")
def knowledge_stats(role: str = Depends(get_current_user)):
    kb = get_kb()
    return {"chunks": len(kb.chunks), "docs_dir": str(kb.docs_dir)}

class DocumentUpload(BaseModel):
    text: str = Field(..., min_length=1, max_length=100000)
    source: str = Field(default="upload.md", min_length=1, max_length=200)

@app.post("/knowledge/upload")
def knowledge_upload(doc: DocumentUpload, role: str = Depends(require_admin)):
    """上传文档到知识库并重建向量/图谱索引（管理员）。"""
    return rag_mod.upload_document(doc.text, doc.source)

@app.post("/rag/vector-search")
def rag_vector_search(q: Question, role: str = Depends(get_current_user)):
    """向量检索：余弦相似度召回知识片段（登录用户）。"""
    return {"query": q.question, "results": rag_mod.vector_search(q.question, top_k=q.top_k)}

@app.post("/rag/graph")
def rag_graph(q: Question, role: str = Depends(get_current_user)):
    """知识图谱关联查询（登录用户）。"""
    return {"topic": q.question, "entities": get_kb().related_entities(q.question, top_k=q.top_k)}

if __name__ == "__main__":
    import uvicorn
    # 遵循部署约定：监听 0.0.0.0，端口由 PORT 环境变量覆盖，默认 8000
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port)
