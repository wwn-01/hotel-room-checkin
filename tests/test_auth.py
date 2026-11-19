"""分层权限模块单元测试（admin/user + RBAC + JWT，纯标准库）。

运行：python -m unittest tests.test_auth -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import auth  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class TestPassword(unittest.TestCase):
    def test_hash_and_verify(self):
        h = auth.hash_password("admin123")
        self.assertIn("$", h)
        self.assertTrue(auth.verify_password("admin123", h))
        self.assertFalse(auth.verify_password("wrong", h))

    def test_bad_stored(self):
        self.assertFalse(auth.verify_password("x", "not-a-valid-hash"))


class TestToken(unittest.TestCase):
    def test_roundtrip(self):
        tok = auth.create_access_token("admin", auth.ROLE_ADMIN)
        payload = auth.decode_access_token(tok)
        self.assertEqual(payload["sub"], "admin")
        self.assertEqual(payload["role"], auth.ROLE_ADMIN)
        self.assertEqual(payload["tenant"], auth.DEFAULT_TENANT)

    def test_custom_tenant(self):
        tok = auth.create_access_token("u", auth.ROLE_USER, tenant="hotel-a")
        self.assertEqual(auth.decode_access_token(tok)["tenant"], "hotel-a")

    def test_invalid_token(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.decode_access_token("bad.token.here")
        self.assertEqual(ctx.exception.status_code, 401)


class TestRBAC(unittest.TestCase):
    def test_admin_has_all(self):
        for perm in (auth.Permission.ROOM_WRITE, auth.Permission.LOG_READ,
                     auth.Permission.USER_MANAGE, auth.Permission.KB_WRITE):
            self.assertTrue(auth.role_has(auth.ROLE_ADMIN, perm))

    def test_user_read_only(self):
        self.assertTrue(auth.role_has(auth.ROLE_USER, auth.Permission.ROOM_READ))
        self.assertTrue(auth.role_has(auth.ROLE_USER, auth.Permission.RAG_ASK))
        self.assertFalse(auth.role_has(auth.ROLE_USER, auth.Permission.ROOM_WRITE))
        self.assertFalse(auth.role_has(auth.ROLE_USER, auth.Permission.LOG_READ))
        self.assertFalse(auth.role_has(auth.ROLE_USER, auth.Permission.USER_MANAGE))

    def test_unknown_role_downgrade(self):
        self.assertEqual(auth.permissions_for("hacker"), auth.ROLE_PERMISSIONS[auth.ROLE_USER])


class TestPrincipal(unittest.TestCase):
    def test_from_payload(self):
        p = auth._make_principal({"sub": "admin", "role": "admin", "tenant": "hotel-b"})
        self.assertEqual(p.role, auth.ROLE_ADMIN)
        self.assertEqual(p.tenant, "hotel-b")
        self.assertTrue(p.has(auth.Permission.ROOM_WRITE))

    def test_unknown_role_becomes_user(self):
        p = auth._make_principal({"sub": "x", "role": "superadmin"})
        self.assertEqual(p.role, auth.ROLE_USER)


class TestDependencies(unittest.TestCase):
    def test_require_permission_pass(self):
        admin = auth._make_principal({"sub": "a", "role": "admin"})
        dep = auth.require_permission(auth.Permission.LOG_READ)
        self.assertEqual(dep(admin), admin)

    def test_require_permission_denied(self):
        user = auth._make_principal({"sub": "u", "role": "user"})
        dep = auth.require_permission(auth.Permission.LOG_READ)
        with self.assertRaises(HTTPException) as ctx:
            dep(user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_role(self):
        dep = auth.require_role(auth.ROLE_ADMIN)
        admin = auth._make_principal({"sub": "a", "role": "admin"})
        user = auth._make_principal({"sub": "u", "role": "user"})
        self.assertEqual(dep(admin), admin)
        with self.assertRaises(HTTPException) as ctx:
            dep(user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_admin_compat(self):
        admin = auth._make_principal({"sub": "a", "role": "admin"})
        user = auth._make_principal({"sub": "u", "role": "user"})
        self.assertEqual(auth.require_admin(admin), "admin")
        with self.assertRaises(HTTPException) as ctx:
            auth.require_admin(user)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
