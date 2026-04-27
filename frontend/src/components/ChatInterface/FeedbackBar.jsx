/**
 * 反馈工具栏组件
 *
 * 用于用户对 AI 响应进行评价：
 * - 👍 点赞（5 星）
 * - 👎 点踩（1 星）
 * - ✏️ 打开详细评价弹窗（可选评分 + 评论）
 *
 * 提交后显示"感谢您的反馈"确认提示。
 */
import React, { useState } from 'react';
import { Tooltip, Modal, Rate, Input, message as antdMessage } from 'antd';
import { LikeOutlined, DislikeOutlined, EditOutlined } from '@ant-design/icons';
import { submitFeedback } from '../../services/evalService';

const { TextArea } = Input;

function FeedbackBar({ messageId, metadata, agentName }) {
  // Check if feedback already exists in metadata
  const hasExistingFeedback = metadata && metadata.feedback;
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(hasExistingFeedback);
  const [modalVisible, setModalVisible] = useState(false);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');

  // 快速点赞：直接提交 5 星
  const handleLike = async () => {
    try {
      await submitFeedback(messageId, 5, '', agentName, 'like');
      setFeedbackSubmitted(true);
      antdMessage.success('感谢您的反馈 👍');
    } catch (error) {
      antdMessage.error('提交反馈失败');
    }
  };

  // 快速点踩：直接提交 1 星
  const handleDislike = async () => {
    try {
      await submitFeedback(messageId, 1, '', agentName, 'dislike');
      setFeedbackSubmitted(true);
      antdMessage.info('感谢您的反馈，我们会持续改进');
    } catch (error) {
      antdMessage.error('提交反馈失败');
    }
  };

  // 打开详细评价弹窗
  const handleOpenModal = () => {
    setRating(0);
    setComment('');
    setModalVisible(true);
  };

  // 提交详细评价
  const handleSubmitFeedback = async () => {
    if (rating === 0) {
      antdMessage.warning('请先选择评分');
      return;
    }
    try {
      await submitFeedback(messageId, rating, comment, agentName, 'detail');
      setFeedbackSubmitted(true);
      setModalVisible(false);
      antdMessage.success('感谢您的反馈！');
    } catch (error) {
      antdMessage.error('提交反馈失败');
    }
  };

  if (feedbackSubmitted) {
    return (
      <div style={{
        fontSize: '11px',
        color: '#52c41a',
        marginTop: '2px',
        paddingLeft: '2px',
      }}>
        ✓ 反馈已提交
      </div>
    );
  }

  return (
    <>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        marginTop: '4px',
        paddingLeft: '2px',
      }}>
        <Tooltip title="有帮助">
          <LikeOutlined
            onClick={handleLike}
            style={{
              fontSize: '14px',
              color: '#999',
              cursor: 'pointer',
              transition: 'color 0.2s',
            }}
            onMouseEnter={(e) => (e.target.style.color = '#52c41a')}
            onMouseLeave={(e) => (e.target.style.color = '#999')}
          />
        </Tooltip>
        <Tooltip title="需改进">
          <DislikeOutlined
            onClick={handleDislike}
            style={{
              fontSize: '14px',
              color: '#999',
              cursor: 'pointer',
              transition: 'color 0.2s',
            }}
            onMouseEnter={(e) => (e.target.style.color = '#ff4d4f')}
            onMouseLeave={(e) => (e.target.style.color = '#999')}
          />
        </Tooltip>
        <Tooltip title="详细评价">
          <EditOutlined
            onClick={handleOpenModal}
            style={{
              fontSize: '13px',
              color: '#bbb',
              cursor: 'pointer',
            }}
          />
        </Tooltip>
      </div>

      <Modal
        title="评价此响应"
        open={modalVisible}
        onOk={handleSubmitFeedback}
        onCancel={() => setModalVisible(false)}
        okText="提交"
        cancelText="取消"
      >
        <div style={{ marginBottom: '16px' }}>
          <div style={{ marginBottom: '8px', fontSize: '13px', color: '#666' }}>评分</div>
          <Rate onChange={setRating} value={rating} />
        </div>
        <div>
          <div style={{ marginBottom: '8px', fontSize: '13px', color: '#666' }}>详细评价（可选）</div>
          <TextArea
            rows={3}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="请分享您的使用体验..."
            maxLength={500}
            showCount
          />
        </div>
      </Modal>
    </>
  );
}

export default FeedbackBar;
