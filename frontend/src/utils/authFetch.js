// 统一认证请求封装：支持独立登录 + 子应用（Wujie/iframe）两种 token 来源
// 所有原生 fetch 走 authFetch，自动携带 Authorization: Bearer，替代 X-User-Id 直传
import store from 'store2';

/**
 * 获取当前请求 token（优先级与 request.js 拦截器一致，但独立登录在生产也生效）
 * 1. wujie props（子应用嵌入，宿主注入）
 * 2. URL hash sso_token（原生 iframe）
 * 3. localStorage __SRMC_Config_token（独立登录）→ 外部系统 AccessToken → token → SSO
 */
export function getAuthToken() {
  let token = window.$wujie?.props?.token;
  if (!token) {
    const hash = window.location.hash || '';
    const m = hash.match(/sso_token=([^&]+)/);
    if (m) token = decodeURIComponent(m[1]);
  }
  if (!token) {
    token = store('__SRMC_Config_token')
      || localStorage.getItem('__SYSTEM_Data_AccessToken')
      || localStorage.getItem('token')
      || localStorage.getItem('__bp_sso_token__');
  }
  return token || '';
}

/**
 * 带 Bearer 认证的原生 fetch 封装。
 * 用法与 fetch 一致，自动加 Authorization: Bearer <token>。
 */
export function authFetch(url, options = {}) {
  const token = getAuthToken();
  const headers = { ...(options.headers || {}) };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers }).then((resp) => {
    // 401：通知 App 弹登录（独立登录模式；子应用由宿主处理，App 侧会过滤）
    if (resp.status === 401) {
      window.dispatchEvent(new CustomEvent('fc:auth-required'));
    }
    return resp;
  });
}
