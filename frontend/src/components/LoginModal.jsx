import React, { useState } from 'react';
import { Modal, Form, Input, Button, message } from 'antd';
import { UserOutlined, LockOutlined, HomeOutlined } from '@ant-design/icons';
import store from 'store2';

export default function LoginModal({ open, onClose, onLoginSuccess }) {
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const handleLogin = async (values) => {
    setLoading(true);
    try {
      const oauthResp = await fetch('/SysWebApi/api/OAuth/Authenticate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          Domain: 'local',
          UserAccount: values.UserAccount,
          Password: values.Password,
          plantCode: values.plantCode,
        }),
      });
      const oauthData = await oauthResp.json();

      if (!oauthData.IsSuccess) {
        message.error(oauthData.Message || '认证失败');
        return;
      }

      const token = oauthData.Data?.AccessToken;
      const loginUserName = oauthData.Data?.TokenProfile?.LoginUserName || '';

      if (!token) {
        message.error('MES 未返回 AccessToken');
        return;
      }

      let userInfo = {
        NowLoginUser: loginUserName,
        UserAccount: values.UserAccount,
        RealName: loginUserName,
        NowPlantCode: values.plantCode,
      };

      try {
        const infoResp = await fetch(
          `/SysWebApi/api/LoginUserAuthInfo/CurrentUserInfo?plantCode=${encodeURIComponent(values.plantCode)}&loginUserName=${encodeURIComponent(loginUserName)}`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        const infoData = await infoResp.json();
        if (infoData.NowLoginUser) {
          userInfo = {
            NowLoginUser: infoData.NowLoginUser || loginUserName,
            UserAccount: infoData.UserAccount || values.UserAccount,
            RealName: infoData.RealName || loginUserName,
            NowPlantCode: infoData.NowPlantCode || values.plantCode,
          };
        }
      } catch {
        // 用户详情接口失败不影响登录
      }

      store('__SRMC_Config_token', token);
      store('__SRMC_Data_user', userInfo);

      message.success(`登录成功，欢迎 ${userInfo.RealName || loginUserName}`);
      form.resetFields();
      onLoginSuccess?.(userInfo);
      onClose();
    } catch (e) {
      message.error('登录失败，请检查网络连接');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="员工登录"
      open={open}
      onCancel={onClose}
      footer={null}
      width={380}
      destroyOnHidden
      centered
    >
      <div style={{ padding: '12px 0' }}>
        <div style={{
          textAlign: 'center', marginBottom: 24,
          color: '#9498ae', fontSize: 13,
        }}>
          请输入 MES 账号和密码登录系统
        </div>

        <Form form={form} onFinish={handleLogin} size="large" autoComplete="off">
          <Form.Item
            name="UserAccount"
            rules={[{ required: true, message: '请输入用户名!' }]}
          >
            <Input
              prefix={<UserOutlined style={{ color: '#b8bcc8' }} />}
              placeholder="用户名"
              autoFocus
            />
          </Form.Item>

          <Form.Item
            name="Password"
            rules={[{ required: true, message: '请输入密码!' }]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: '#b8bcc8' }} />}
              placeholder="密码"
            />
          </Form.Item>

          <Form.Item
            name="plantCode"
            rules={[{ required: true, message: '请输入工厂编码!' }]}
          >
            <Input
              prefix={<HomeOutlined style={{ color: '#b8bcc8' }} />}
              placeholder="工厂编码"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
              style={{ height: 40, fontSize: 15 }}
            >
              登录
            </Button>
          </Form.Item>
        </Form>

        <div style={{
          textAlign: 'center', marginTop: 16,
          color: '#b8bcc8', fontSize: 12,
        }}>
          测试阶段 — 生产环境由父应用统一认证
        </div>
      </div>
    </Modal>
  );
}

export { LoginModal };
