"""分层权限系统 —— SaaS 风格 RBAC + JWT 鉴权。

角色：admin（管理员）/ user（普通用户）。
设计要点（向后兼容原有 admin/user 契约）：
- 保留 get_current_user / require_admin / create_access_token 等原有函数签名。
- 新增 RBAC 权限点（Permission）与角色-权限映射（ROLE_PERMISSIONS），支持细粒度鉴权。
- 新增 Principal（身份主体）携带 user/role/tenant，预留多租户（SaaS）扩展位。
- 演示阶段 JWT 里 role 决定权限；真实接入只需替换数据源，鉴权逻辑无需改动。

依赖注入用法：
    role = Depends(get_current_user)          # 任意已登录用户，返回角色字符串（兼容旧代码）
    require_permission("room:write")         # 细粒度：具备该权限点才放行
    require_admin                            # 管理员专属（= require_role("admin")）
    principal = Depends(get_principal)       # 拿到完整身份主体（含 tenant）
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, FrozenSet, List, Optional

import jwt
from fastapi import Depends, Header, HTTPException, status

# ---------------- 配置 ----------------
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "hotel-mvp-demo-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
_PBKDF2_ROUNDS = 100_000

ROLE_ADMIN = "admin"
ROLE_USER = "user"

DEFAULT_TENANT = "default"  # 单租户演示默认值；多租户时由登录数据注入


# ---------------- RBAC 权限点 ----------------
class Permission:
    """权限点常量。命名遵循 资源:动作，便于按业务扩展。"""
    ROOM_READ = "room:read"
    ROOM_WRITE = "room:write"
    WORKORDER_CREATE = "workorder:create"
    LOG_READ = "log:read"
    KB_READ = "kb:read"
    KB_WRITE = "kb:write"
    RAG_ASK = "rag:ask"
    CHECKIN_VERIFY = "checkin:verify"
    USER_MANAGE = "user:manage"


_ALL_PERMISSIONS: FrozenSet[str] = frozenset({
    Permission.ROOM_READ, Permission.ROOM_WRITE, Permission.WORKORDER_CREATE,
    Permission.LOG_READ, Permission.KB_READ, Permission.KB_WRITE,
    Permission.RAG_ASK, Permission.CHECKIN_VERIFY, Permission.USER_MANAGE,
})

# 角色 -> 权限点映射。user 只读+问答，admin 拥有全部权限。
ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    ROLE_USER: frozenset({
        Permission.ROOM_READ, Permission.RAG_ASK, Permission.CHECKIN_VERIFY,
    }),
    ROLE_ADMIN: _ALL_PERMISSIONS,
}


def permissions_for(role: str) -> FrozenSet[str]:
    """返回某角色拥有的权限点；未知角色降级为 user 权限集。"""
    return ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS[ROLE_USER])


def role_has(role: str, permission: str) -> bool:
    """判断角色是否具备某权限点。"""
    return permission in permissions_for(role)


# ---------------- 身份主体 ----------------
@dataclass(frozen=True)
class Principal:
    """已认证身份主体。tenant 为 SaaS 多租户预留字段。"""
    username: str
    role: str
    tenant: str = DEFAULT_TENANT
    permissions: FrozenSet[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def _make_principal(payload: dict) -> Principal:
    """由 JWT 载荷构造 Principal，角色非法时降级为 user。"""
    username = str(payload.get("sub", "") or "anonymous")
    role = str(payload.get("role", "")).lower()
    if role not in ROLE_PERMISSIONS:
        role = ROLE_USER
    tenant = str(payload.get("tenant", "") or DEFAULT_TENANT)
    return Principal(username=username, role=role, tenant=tenant,
                     permissions=permissions_for(role))


# ---------------- 密码与令牌 ----------------
def hash_password(password: str) -> str:
    """生成 盐$哈希 格式的密码摘要（PBKDF2-HMAC-SHA256，加盐）。"""
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


def create_access_token(username: str, role: str,
                        expires_delta: Optional[timedelta] = None,
                        tenant: str = DEFAULT_TENANT) -> str:
    """签发 JWT，包含 sub / role / tenant / iat / exp。"""
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": username, "role": role, "tenant": tenant,
        "iat": now, "exp": expire,
    }
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


def _bearer_token(authorization: str) -> str:
    """从 Authorization 头提取 Bearer token，缺失或格式错误返回 401。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


# ---------------- 依赖注入 ----------------
def get_principal(authorization: str = Header(default="")) -> Principal:
    """解析令牌，返回完整身份主体（含租户与权限点）。"""
    payload = decode_access_token(_bearer_token(authorization))
    return _make_principal(payload)


def get_current_user(authorization: str = Header(default="")) -> str:
    """兼容旧接口：返回角色字符串（admin/user），未知角色降级为 user。"""
    return get_principal(authorization).role


def require_role(*allowed: str) -> Callable:
    """角色校验依赖工厂：角色不在允许集合内返回 403。"""
    wanted = frozenset(r.lower() for r in allowed)

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role not in wanted:
            raise HTTPException(status_code=403, detail="角色权限不足")
        return principal

    return _dep


def require_permission(permission: str) -> Callable:
    """细粒度权限依赖工厂：主体不具备该权限点返回 403。"""
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
        return principal

    return _dep


def require_admin(principal: Principal = Depends(get_principal)) -> str:
    """管理员专属依赖（兼容旧签名，返回角色字符串）。"""
    if principal.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal.role
