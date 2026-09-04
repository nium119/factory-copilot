"""统一认证依赖：从 Authorization Bearer JWT 验签解析当前用户。

安全修复：移除 X-User-Id 直传（客户端可伪造身份）。所有 API 只信任
Bearer token 验签（签名 + 过期）后的用户身份；无有效 token 返回 401。
"""
from fastapi import HTTPException, Request

from app.services.auth_service import auth_service


def get_current_user_id(request: Request) -> str:
    """从 Authorization Bearer 解析当前用户 id（JWT 验签 + 过期），失败 401。

    解析成功后同步设置请求上下文 ContextVar（_request_user_id/_request_token），
    供 multi_system_backend / 日志 / 数据授权等下游使用（所有端点统一生效）。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未认证：缺少 Bearer token")
    token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="未认证：token 为空")
    user_id = auth_service.resolve_user(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="未认证：token 无效或已过期")
    # 设置请求上下文（端点上下文内，可靠传给下游）
    from app.services.multi_system_backend import _request_user_id, _request_token, _request_claims
    from app.services.multi_system_backend import _parse_jwt_claims
    _request_user_id.set(user_id)
    _request_token.set(token)
    # token 里携带工厂与用户信息：解析后缓存，供数据授权（Scope/DataFilter）
    # 与 MES API 参数替换（get_session_value）取值
    _request_claims.set(_parse_jwt_claims(token))
    return user_id
