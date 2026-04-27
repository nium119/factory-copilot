import axios from 'axios';
import store from 'store2';

// API基础配置
const request = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    // 优先读取 MES Admin 共用的 token（store2），回退到 localStorage
    const token = store('__SRMC_Config_token') || localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    return response.data;
  },
  (error) => {
    // 统一错误处理
    if (error.response) {
      switch (error.response.status) {
        case 401:
          // 未授权,跳转到登录页
          console.error('未授权,请重新登录');
          break;
        case 403:
          console.error('没有权限访问');
          break;
        case 404:
          console.error('请求的资源不存在');
          break;
        case 500:
          console.error('服务器错误');
          break;
        default:
          console.error('请求失败:', error.response.data);
      }
    } else if (error.request) {
      console.error('网络错误,请检查网络连接');
    } else {
      console.error('请求配置错误:', error.message);
    }
    return Promise.reject(error);
  }
);

export default request;
