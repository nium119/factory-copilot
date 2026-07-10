"""认证 API — 员工登录/登出/获取当前用户。

测试阶段提供直接端点，绕过 Agent 确保可靠性。
生产环境由父应用认证接管。
"""
import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.logger import log

router = APIRouter(prefix="/auth", tags=["认证"])


class LoginRequest(BaseModel):
    empCode: str
    password: str
    plantCode: str = ""


class LoginResponse(BaseModel):
    success: bool
    token: str = ""
    user: dict = {}
    message: str = ""


@router.post("/login", summary="员工登录")
async def login(req: LoginRequest):
    """使用工号和密码登录 MES 系统。

    调用 MES OAuth API 获取 AccessToken，然后查询用户详情。
    成功后注册 auth_service 会话，后续请求可通过 Bearer token 识别用户。
    """
    base_url = settings.MES_API_BASE_URL.rstrip("/") if settings.MES_API_BASE_URL else ""
    if not base_url:
        log.warning("[Auth] MES_API_BASE_URL 未配置，使用模拟登录")
        return _mock_login(req)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # 步骤 1: 调用 OAuth Authenticate
            oauth_url = f"{base_url}/SysWebApi/api/OAuth/Authenticate"
            oauth_body = {
                "Domain": req.plantCode or "local",
                "UserAccount": req.empCode,
                "Password": req.password,
                "plantCode": req.plantCode or "",
            }
            log.info(f"[Auth] 登录请求: {req.empCode} → {oauth_url}")

            oauth_resp = await client.post(oauth_url, json=oauth_body)
            oauth_data = oauth_resp.json()

            if not oauth_data.get("IsSuccess"):
                msg = oauth_data.get("Message", "认证失败")
                log.warning(f"[Auth] OAuth 失败: {msg}")
                return LoginResponse(success=False, message=str(msg))

            d = oauth_data.get("Data", {})
            token = d.get("AccessToken", "")
            profile = d.get("TokenProfile", {})
            login_user_name = profile.get("LoginUserName", "")

            if not token:
                return LoginResponse(success=False, message="MES 未返回 AccessToken")

            # 步骤 2: 获取用户详情
            info_url = f"{base_url}/SysWebApi/api/LoginUserAuthInfo/CurrentUserInfo"
            info_params = {
                "plantCode": req.plantCode or "",
                "loginUserName": login_user_name,
            }
            info_resp = await client.get(
                info_url, params=info_params,
                headers={"Authorization": f"Bearer {token}"},
            )
            info_data = info_resp.json() if info_resp.status_code == 200 else {}

            # 注册会话
            from app.services.auth_service import auth_service as _auth_svc
            user_info = {
                "NowLoginUser": login_user_name,
                "UserAccount": req.empCode,
                "RealName": info_data.get("RealName", login_user_name),
                "NowPlantCode": info_data.get("NowPlantCode", req.plantCode),
            }

            _auth_svc.register_session(token, login_user_name, user_info)
            log.info(f"[Auth] 登录成功: {login_user_name}")
            return LoginResponse(success=True, token=token, user=user_info)

    except httpx.TimeoutException:
        log.error("[Auth] MES API 超时")
        return LoginResponse(success=False, message="MES 认证服务超时，请稍后重试")
    except Exception as e:
        log.error(f"[Auth] 登录异常: {e}")
        return LoginResponse(success=False, message=f"登录失败: {str(e)}")


def _mock_login(req: LoginRequest) -> LoginResponse:
    """模拟登录 — MES_API_BASE_URL 未配置时的回退。"""
    from app.services.auth_service import auth_service as _auth_svc

    mock_token = f"mock_token_{req.empCode}"
    mock_user = {
        "NowLoginUser": req.empCode,
        "UserAccount": req.empCode,
        "RealName": req.empCode,
        "NowPlantCode": req.plantCode or "mock",
    }
    _auth_svc.register_session(mock_token, req.empCode, mock_user)
    log.info(f"[Auth] 模拟登录: {req.empCode}")
    return LoginResponse(success=True, token=mock_token, user=mock_user)


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
    """返回当前登录用户信息及角色。"""
    user_id = request.headers.get("X-User-Id", "").strip()
    if user_id:
        from app.services.auth_service import auth_service as _auth_svc
        roles = await _auth_svc.get_effective_roles(user_id)
        return {"success": True, "user": {"UserAccount": user_id}, "roles": list(roles)}
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        from app.services.auth_service import auth_service as _auth_svc
        user_id = _auth_svc.resolve_user(token)
        if user_id:
            roles = await _auth_svc.get_effective_roles(user_id)
            return {"success": True, "user": {"UserAccount": user_id}, "roles": list(roles)}
    return {"success": False, "user": None, "message": "未登录"}
