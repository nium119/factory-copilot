/**
 * 反馈 API 服务
 *
 * 用于提交用户对 AI 响应的评价（👍👎 + 1-5 星评分 + 文字评论）。
 * 后端接口：POST /api/eval/feedback
 */
import request from './request';

/**
 * 提交反馈
 * @param {string} messageId - 消息 ID（后端 DB 真实 ID）
 * @param {number} score - 评分，1-5
 * @param {string} comment - 可选，详细评价文字
 * @returns {Promise}
 */
export async function submitFeedback(messageId, score, comment = '') {
  return await request.post('/eval/feedback', {
    message_id: messageId,
    score,
    comment,
  });
}
