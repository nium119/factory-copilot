import React, { useState } from 'react';
import { Input } from 'antd';
import { SearchOutlined, CloseOutlined } from '@ant-design/icons';

/**
 * 搜索栏组件
 */
export default function SearchBar({ onSearch }) {
  const [value, setValue] = useState('');

  const handleChange = (e) => {
    const newValue = e.target.value;
    setValue(newValue);
    onSearch(newValue);
  };

  const handleClear = () => {
    setValue('');
    onSearch('');
  };

  return (
    <Input
      placeholder="搜索对话..."
      prefix={<SearchOutlined />}
      suffix={value && <CloseOutlined onClick={handleClear} style={{ cursor: 'pointer' }} />}
      value={value}
      onChange={handleChange}
      allowClear
    />
  );
}
