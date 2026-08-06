import React, { useState, useEffect, useCallback } from 'react';
import { Spin, Empty, Pagination, Typography, Button, Dropdown, message, Tag } from 'antd';
import { FileTextOutlined, DownOutlined, RightOutlined, ExportOutlined } from '@ant-design/icons';
import MarkdownRenderer from '../MarkdownRenderer';
import { ChangePlanPanel } from '../ChatInterface/MessageItem';
import { authFetch } from '../../utils/authFetch';
import request from '../../services/request';

export default function ReportHistoryView() {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [expanded, setExpanded] = useState({});
  const pageSize = 20;

  const fetchReports = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request.get(`/messages/reports?page=${page}&page_size=${pageSize}`);
      setReports(data.reports || []);
      setTotal(data.total || 0);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [page]);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  const toggleExpand = (id) => setExpanded(prev => ({ ...prev, [id]: !prev[id] }));

  // 清理 markdown 语法，提取纯文本预览
  const cleanPreview = (md) => {
    if (!md) return '(无内容)';
    return md
      .replace(/```[\s\S]*?```/g, ' ')   // 代码块
      .replace(/#{1,6}\s+/g, '')         // 标题
      .replace(/\*\*(.+?)\*\*/g, '$1')   // 粗体
      .replace(/\*(.+?)\*/g, '$1')       // 斜体
      .replace(/`(.+?)`/g, '$1')         // 行内代码
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // 链接
      .replace(/\|/g, ' ')               // 表格
      .replace(/[-*_]{3,}/g, '')         // 分隔线
      .replace(/<br\s*\/?>/gi, ' ')      // <br>
      .replace(/\n+/g, ' ')              // 换行
      .replace(/\s{2,}/g, ' ')           // 多余空格
      .substring(0, 100)                 // 截断
      .trim();
  };

  const handleExport = async (reportId, format) => {
    const url = `${window.__API_BASE__}/messages/reports/${reportId}/export?format=${format}`;
    if (format === 'pdf') {
      window.open(url, '_blank');
    } else {
      try {
        const resp = await authFetch(url);
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          message.error(err.detail || `${format.toUpperCase()} 导出失败，请重试`);
          return;
        }
        const blob = await resp.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `report.${format}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(downloadUrl);
        message.success(`正在下载 ${format.toUpperCase()} 文件`);
      } catch (e) {
        message.error('下载失败: ' + (e.message || '网络错误'));
      }
    }
  };

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: '#f5f5f7' }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>历史分析报告</h2>

        {loading ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        ) : reports.length === 0 ? (
          <Empty description="暂无分析报告" style={{ padding: 60 }} />
        ) : (
          <>
            {reports.map(item => {
              const isExpanded = expanded[item.id];
              return (
              <div key={item.id} style={{
                background: '#fff', borderRadius: 8, marginBottom: 8,
                border: '1px solid #e8e8ec', overflow: 'hidden',
              }}>
                <div onClick={() => toggleExpand(item.id)}
                  style={{ padding: '12px 16px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10 }}>
                  <FileTextOutlined style={{ fontSize: 18, color: '#6c5ce7', flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 2 }}>
                      {item.title || '分析报告'}
                    </div>
                    <Typography.Text style={{ fontSize: 12, color: '#8c8c8c' }} ellipsis>
                      {cleanPreview(item.content)}
                    </Typography.Text>
                    <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                      {item.created_at ? new Date(item.created_at).toLocaleString() : ''}
                    </div>
                  </div>
                  <Dropdown menu={{ items: [
                    { key: 'pdf', label: '导出 PDF', onClick: (e) => { e.domEvent.stopPropagation(); handleExport(item.id, 'pdf'); } },
                    { key: 'docx', label: '导出 Word', onClick: (e) => { e.domEvent.stopPropagation(); handleExport(item.id, 'docx'); } },
                  ] }} trigger={['click']}>
                    <Button size="small" icon={<ExportOutlined />} onClick={(e) => e.stopPropagation()}>导出</Button>
                  </Dropdown>
                  {isExpanded ? <DownOutlined style={{ color: '#999' }} /> : <RightOutlined style={{ color: '#999' }} />}
                </div>
                {isExpanded && (
                  <div style={{
                    padding: '16px 20px', borderTop: '1px solid #f0f0f0', fontSize: 14,
                    lineHeight: 1.8, color: '#333', maxHeight: '70vh', overflow: 'auto',
                  }}>
                    <MarkdownRenderer content={(item.content || '').replace(/```(?:json)?\s*\n[\s\S]*?\n```/g, '')} />
                    {item.metadata?.change_plans?.length > 0 && (
                      <ChangePlanPanel plans={item.metadata.change_plans} savedResults={item.metadata.plan_exec_results || {}} />
                    )}
                  </div>
                )}
              </div>
            )})}
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <Pagination size="small" current={page} total={total} pageSize={pageSize}
                onChange={setPage} showTotal={t => `共 ${t} 条`} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
