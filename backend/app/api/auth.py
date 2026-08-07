"""认证 API — 登出/获取当前用户。

登录不在此处：前端登录经 /SysWebApi 反向代理（main.py 转发到 MES OAuth），
MES 与 FC 共享签名密钥（JWT_SECRET），FC 直接用该密钥验签 MES AccessToken，
无需 session / 换发。用户识别走 app.api.deps.get_current_user_id（Bearer JWT 验签）。
"""
from fastapi import APIRouter, Request

from app.core.logger import log

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/logout", summary="退出登录")
async def logout(request: Request):
    """清除当前用户的登录会话。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        from app.services.auth_service import auth_service as _auth_svc
        _auth_svc.clear_session(token)
        return {"success": True, "message": "已退出登录"}
    return {"success": True, "message": "无需退出（未登录）"}


@router.get("/me", summary="获取当前用户")
async def get_current_user(request: Request):
    """返回当前登录用户信息及角色（Bearer JWT 验签）。

    安全修复：移除 X-User-Id 直传，验签失败返回 401。
    """
    from app.api.deps import get_current_user_id as _resolve
    from app.services.auth_service import auth_service as _auth_svc
    user_id = _resolve(request)  # 验签失败抛 401
    roles = await _auth_svc.get_effective_roles(user_id)
    return {"success": True, "user": {"UserAccount": user_id}, "roles": list(roles)}
