import React, { useState, useEffect, useCallback } from 'react';
import { Spin, Empty, Pagination, Typography } from 'antd';
import { FileTextOutlined, DownOutlined, RightOutlined } from '@ant-design/icons';
import { marked } from 'marked';

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
      const resp = await fetch(`/api/messages/reports?page=${page}&page_size=${pageSize}`);
      const data = await resp.json();
      setReports(data.reports || []);
      setTotal(data.total || 0);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [page]);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  const toggleExpand = (id) => setExpanded(prev => ({ ...prev, [id]: !prev[id] }));

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', background: '#f5f5f7' }}>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>📊 历史分析报告</h2>

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
                      {item.content?.substring(0, 80)?.replace(/\n/g, ' ') || '(无内容)'}
                    </Typography.Text>
                    <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                      {item.created_at ? new Date(item.created_at).toLocaleString() : ''}
                    </div>
                  </div>
                  {isExpanded ? <DownOutlined style={{ color: '#999' }} /> : <RightOutlined style={{ color: '#999' }} />}
                </div>
                {isExpanded && (
                  <div style={{
                    padding: '16px 20px', borderTop: '1px solid #f0f0f0', fontSize: 14,
                    lineHeight: 1.8, color: '#333', maxHeight: '70vh', overflow: 'auto',
                  }}
                  className="markdown-body"
                  dangerouslySetInnerHTML={{ __html: marked.parse(item.content || '') }}
                  />
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
