import React, { useState, useRef } from 'react';
import { Input, Button, Space, Tooltip, Upload } from 'antd';
import { SendOutlined, AudioOutlined, PaperClipOutlined, PictureOutlined, ThunderboltOutlined, BulbOutlined } from '@ant-design/icons';

const { TextArea } = Input;

function ChatInput({ onSend, disabled, placeholder = "输入消息... (Enter发送, Shift+Enter换行)" }) {
  const [inputValue, setInputValue] = useState('');
  const [deepThink, setDeepThink] = useState(false);
  const inputRef = useRef(null);

  const handleSend = () => {
    if (!inputValue.trim()) return;
    onSend(inputValue, { deepThink });
    setInputValue('');
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div style={{ 
      display: 'flex', 
      alignItems: 'flex-end', 
      gap: '8px', 
      padding: '12px 16px',
      background: '#fff',
      borderRadius: '12px',
      border: '1px solid #e8e8e8',
      boxShadow: '0 2px 8px rgba(0,0,0,0.06)'
    }}>
      {/* 左侧功能按钮 */}
      <Space direction="vertical" size={4}>
        <Tooltip title={deepThink ? "关闭深度思考" : "开启深度思考"}>
          <Button
            type={deepThink ? "primary" : "default"}
            size="small"
            icon={<BulbOutlined />}
            onClick={() => setDeepThink(!deepThink)}
            style={{ fontSize: '12px' }}
          >
            深度思考
          </Button>
        </Tooltip>
      </Space>

      {/* 输入框 */}
      <TextArea
        ref={inputRef}
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyPress={handleKeyPress}
        placeholder={placeholder}
        autoSize={{ minRows: 1, maxRows: 4 }}
        style={{ 
          flex: 1, 
          borderRadius: '8px',
          border: '1px solid #d9d9d9',
          padding: '8px 12px'
        }}
        disabled={disabled}
      />

      {/* 右侧功能按钮 */}
      <Space size={4}>
        <Tooltip title="语音输入">
          <Button 
            size="small" 
            icon={<AudioOutlined />}
            style={{ border: 'none' }}
          />
        </Tooltip>
        
        <Upload showUploadList={false}>
          <Tooltip title="上传文件">
            <Button 
              size="small" 
              icon={<PaperClipOutlined />}
              style={{ border: 'none' }}
            />
          </Tooltip>
        </Upload>
        
        <Upload showUploadList={false} accept="image/*">
          <Tooltip title="上传图片">
            <Button 
              size="small" 
              icon={<PictureOutlined />}
              style={{ border: 'none' }}
            />
          </Tooltip>
        </Upload>

        <Tooltip title="发送消息">
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            disabled={disabled || !inputValue.trim()}
            style={{ 
              borderRadius: '8px',
              height: '36px'
            }}
          />
        </Tooltip>
      </Space>
    </div>
  );
}

export default ChatInput;
