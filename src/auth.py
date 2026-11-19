"""JWT 分层权限系统 —— 管理员(admin) / 普通用户(user)。

演示模式约定：
- 密钥支持环境变量 JWT_SECRET_KEY 覆盖，默认内置演示密钥（上线务必替换）。
- 密码用标准库 PBKDF2-HMAC-SHA256 加盐哈希，不引入额外依赖。
- POST /login 用用户名密码换取 access_token，之后用 Authorization: Bearer <token> 访问。
- 真人接入时只需替换 authenticate_user 的数据源和密钥管理，其余代码无需改动。
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Depends, status

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "hotel-mvp-demo-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
_PBKDF2_ROUNDS = 100_000

ROLE_ADMIN = "admin"
ROLE_USER = "user"


def hash_password(password: str) -> str:
    """生成 盐$哈希 格式的密码摘要。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码，任何异常都判定为失败。"""
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS
        )
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def create_access_token(username: str, role: str, expires_delta: Optional[timedelta] = None) -> str:
    """签发 JWT，包含 sub(用户名)、role(角色)、iat、exp。"""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {"sub": username, "role": role, "iat": now, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解析并校验 JWT，失败返回 401。"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(authorization: str = Header(default="")) -> str:
    """从 Authorization: Bearer <token> 解析角色；未携带或非法一律拒绝。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    role = str(payload.get("role", "")).lower()
    if role not in (ROLE_ADMIN, ROLE_USER):
        role = ROLE_USER  # 未知角色降级为最低权限
    return role


def require_admin(role: str = Depends(get_current_user)) -> str:
    """管理员专属依赖：非 admin 返回 403。"""
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return role
