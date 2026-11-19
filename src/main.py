from fastapi import FastAPI, Body
from pydantic import BaseModel
import sqlite3
import time
from typing import Optional, List

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
        role TEXT
    )
    ''')
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
    cur.execute("INSERT OR IGNORE INTO user(username,role) VALUES ('front','前台'),('qc','质检'),('admin','管理员')")
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

# 日志记录
def add_log(operator:str, content:str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO audit_log(operator,content,create_time) VALUES (?,?,?)",(operator,content,time.time()))
    conn.commit()
    conn.close()

# 接口
@app.get("/rooms")
def get_all_rooms():
    conn = sqlite3.connect(DB_FILE)
    res = conn.execute("SELECT * FROM room").fetchall()
    conn.close()
    return {"rooms": res}

@app.get("/room/{room_id}")
def get_room(room_id:int):
    conn = sqlite3.connect(DB_FILE)
    r = conn.execute("SELECT * FROM room WHERE id=?",(room_id,)).fetchone()
    conn.close()
    return {"room": r}

@app.post("/workorder")
def create_workorder(wo: WorkOrderCreate):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO workorder(room_id,wtype,status,creator,result) VALUES (?,?,?,?,?)",
                (wo.room_id, wo.wtype, "新建", wo.creator, wo.result))
    conn.commit()
    wid = cur.lastrowid
    add_log(wo.creator,f"创建工单 {wid}")
    conn.close()
    return {"workorder_id":wid}

@app.put("/room/{room_id}/status")
def update_room_status(room_id:int, data: RoomStatusUpdate, operator:str="admin"):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE room SET status=?,remark=? WHERE id=?",(data.status,data.remark,room_id))
    conn.commit()
    add_log(operator, f"房间{room_id}状态更新为 {data.status}")
    conn.close()
    return {"msg":"更新成功"}

@app.get("/logs")
def get_logs():
    conn = sqlite3.connect(DB_FILE)
    res = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    return {"logs":res}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app",host="0.0.0.0",port=8000,reload=True)
