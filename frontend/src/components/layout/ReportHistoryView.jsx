import React, { useState, useEffect, useCallback } from 'react';
import { Spin, Empty, Pagination, List, Typography } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';

export default function ReportHistoryView() {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
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
            <List
              dataSource={reports}
              renderItem={item => (
                <List.Item style={{
                  background: '#fff', borderRadius: 8, padding: '12px 16px',
                  marginBottom: 8, border: '1px solid #e8e8ec', cursor: 'pointer',
                }}>
                  <List.Item.Meta
                    avatar={<FileTextOutlined style={{ fontSize: 20, color: '#6c5ce7' }} />}
                    title={
                      <Typography.Text style={{ fontSize: 13, color: '#333' }}>
                        {item.content?.substring(0, 200) || '(无内容)'}
                      </Typography.Text>
                    }
                    description={
                      <span style={{ fontSize: 11, color: '#999' }}>
                        {item.created_at ? new Date(item.created_at).toLocaleString() : ''}
                        {' · '}点击查看完整对话
                      </span>
                    }
                  />
                </List.Item>
              )}
            />
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <Pagination
                size="small"
                current={page}
                total={total}
                pageSize={pageSize}
                onChange={setPage}
                showTotal={t => `共 ${t} 条`}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
