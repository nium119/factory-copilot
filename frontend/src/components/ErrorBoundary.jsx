import React from 'react';
import { Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: '20px', textAlign: 'center', color: '#999',
          background: '#fff', borderRadius: '8px',
          border: '1px solid #ffccc7', margin: '12px 0',
        }}>
          <p style={{ color: '#ff4d4f', marginBottom: '8px' }}>
            页面渲染异常，请重试
          </p>
          <Button size="small" icon={<ReloadOutlined />} onClick={this.handleRetry}>
            重新加载
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
