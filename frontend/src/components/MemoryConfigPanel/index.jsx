import React, { useEffect } from 'react';
import { Card, Switch, Slider, Button, message } from 'antd';
import { useMemory } from '../../hooks/useMemory';

/**
 * 记忆配置面板组件
 */
export default function MemoryConfigPanel() {
  const {
    memoryConfig,
    fetchMemoryConfig,
    updateMemoryConfig
  } = useMemory();

  // 初始化加载配置
  useEffect(() => {
    fetchMemoryConfig();
  }, [fetchMemoryConfig]);

  // 更新配置
  const handleUpdate = async (key, value) => {
    try {
      await updateMemoryConfig({
        ...memoryConfig,
        [key]: value
      });
      message.success('配置已更新');
    } catch (error) {
      message.error('更新失败');
    }
  };

  return (
    <Card title="记忆配置" className="memory-config-panel">
      {/* 记忆开关 */}
      <div className="config-item">
        <div className="config-label">启用长期记忆</div>
        <Switch
          checked={memoryConfig.enabled}
          onChange={(checked) => handleUpdate('enabled', checked)}
        />
      </div>

      {/* Top-K配置 */}
      <div className="config-item">
        <div className="config-label">
          检索数量 (Top-K): {memoryConfig.top_k}
        </div>
        <Slider
          min={1}
          max={20}
          value={memoryConfig.top_k}
          onChange={(value) => handleUpdate('top_k', value)}
          disabled={!memoryConfig.enabled}
        />
      </div>

      {/* 相似度阈值配置 */}
      <div className="config-item">
        <div className="config-label">
          相似度阈值: {memoryConfig.similarity_threshold.toFixed(2)}
        </div>
        <Slider
          min={0}
          max={1}
          step={0.05}
          value={memoryConfig.similarity_threshold}
          onChange={(value) => handleUpdate('similarity_threshold', value)}
          disabled={!memoryConfig.enabled}
        />
      </div>

      {/* 自动注入开关 */}
      <div className="config-item">
        <div className="config-label">自动注入上下文</div>
        <Switch
          checked={memoryConfig.auto_inject}
          onChange={(checked) => handleUpdate('auto_inject', checked)}
          disabled={!memoryConfig.enabled}
        />
      </div>
    </Card>
  );
}
