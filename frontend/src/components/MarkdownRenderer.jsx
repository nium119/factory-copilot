import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Modal } from 'antd';
import { marked } from 'marked';

// ECharts 懒加载
let echartsInstance = null;
let echartsLoadPromise = null;

async function loadECharts() {
  if (echartsInstance) return echartsInstance;
  if (echartsLoadPromise) return echartsLoadPromise;
  echartsLoadPromise = import('echarts').then(mod => {
    echartsInstance = mod.default || mod;
    return echartsInstance;
  }).catch(err => {
    console.error('ECharts加载失败:', err);
    echartsLoadPromise = null;
    throw err;
  });
  return echartsLoadPromise;
}

// Mermaid 懒加载
let mermaidInstance = null;
let mermaidLoadPromise = null;

async function loadMermaid() {
  if (mermaidInstance) return mermaidInstance;
  if (mermaidLoadPromise) return mermaidLoadPromise;
  mermaidLoadPromise = (async () => {
    try {
      const mod = await import('mermaid');
      const mermaid = mod.default;
      mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose', fontFamily: 'inherit', logLevel: 'fatal' });
      mermaidInstance = mermaid;
      return mermaidInstance;
    } catch (error) {
      console.error('Mermaid加载失败:', error);
      mermaidLoadPromise = null;
      throw error;
    }
  })();
  return mermaidLoadPromise;
}

// 完整渲染 renderer（包含图表容器，用于流式完成后）
const fullRenderer = new marked.Renderer();
const fullOriginalCode = fullRenderer.code.bind(fullRenderer);
let fullChartBlockIndex = 0;

fullRenderer.code = function(code, language, isEscaped) {
  if (language === 'echarts') {
    const idx = fullChartBlockIndex++;
    const encoded = btoa(unescape(encodeURIComponent(code)));
    return `<div class="echarts-chart" data-chart-source="${encoded}" data-chart-idx="${idx}" style="width:100%;height:400px;"></div>`;
  }
  if (language === 'mermaid') {
    const idx = fullChartBlockIndex++;
    const encoded = btoa(unescape(encodeURIComponent(code)));
    return `<div class="mermaid-chart" data-mermaid-source="${encoded}" data-mermaid-idx="${idx}"></div>`;
  }
  return fullOriginalCode(code, language, isEscaped);
};

// 流式输出期间的纯文本渲染：
// 不做markdown解析，只做换行→<br>的简单转换
// 这样DOM结构始终稳定（纯文本+<br>），不会因为markdown解析产生标题/段落突变
function renderPlainText(text) {
  if (!text) return '';
  // 转义HTML特殊字符
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  // 换行→<br>
  return escaped.replace(/\n/g, '<br>');
}

// 完整模式渲染：包含markdown解析和图表容器
function renderFullHtml(text) {
  if (!text) return '';
  fullChartBlockIndex = 0;
  let html = marked.parse(text, { renderer: fullRenderer, breaks: true, gfm: true });
  html = html.replace(/<img /g, '<img class="markdown-image" loading="lazy" ');
  return html;
}

function MarkdownRenderer({ content, streaming = false }) {
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewImage, setPreviewImage] = useState('');
  const [previewTitle, setPreviewTitle] = useState('');
  const containerRef = useRef(null);
  const echartsInstancesRef = useRef([]);

  // 用于追踪渲染模式
  const lastFullContentRef = useRef('');

  const handleImageClick = (e) => {
    if (e.target.tagName === 'IMG' && e.target.classList.contains('markdown-image')) {
      e.preventDefault();
      setPreviewImage(e.target.src);
      setPreviewTitle(e.target.alt || '');
      setPreviewVisible(true);
    }
  };

  // 流式输出期间：纯文本渲染，不做markdown解析
  // 流式完成后：完整markdown渲染（含图表容器），然后初始化图表
  const renderedHtml = useMemo(() => {
    if (streaming) {
      return renderPlainText(content);
    } else {
      lastFullContentRef.current = content;
      return renderFullHtml(content);
    }
  }, [content, streaming]);

  // 图表渲染：只在非streaming模式下执行
  useEffect(() => {
    if (streaming) return;

    // 内容没变化时跳过
    if (content === lastFullContentRef.current && echartsInstancesRef.current.length > 0) return;

    const el = containerRef.current;
    if (!el) return;

    // 先dispose旧的echarts实例（仅销毁尚未被销毁的）
    echartsInstancesRef.current = echartsInstancesRef.current.filter(chart => {
      if (chart && !chart._disposed) {
        try { chart.dispose(); } catch(e) {}
        return false;
      }
      return false;
    });

    // 渲染 ECharts
    const echartsElements = el.querySelectorAll('.echarts-chart');
    if (echartsElements.length > 0) {
      const renderECharts = async () => {
        try {
          const echarts = await loadECharts();
          for (const chartEl of echartsElements) {
            const encoded = chartEl.getAttribute('data-chart-source');
            if (!encoded) continue;

            let option;
            try {
              const jsonStr = decodeURIComponent(escape(atob(encoded)));
              option = JSON.parse(jsonStr);
            } catch (e) {
              try { option = JSON.parse(atob(encoded)); } catch(e2) { continue; }
            }

            try {
              // 先检查并销毁已有的实例，避免重复初始化警告
              const existingChart = echarts.getInstanceByDom(chartEl);
              if (existingChart) {
                existingChart.dispose();
              }
              const chart = echarts.init(chartEl);
              chart.setOption(option);
              echartsInstancesRef.current.push(chart);
            } catch (error) {
              console.error('ECharts渲染失败:', error);
              chartEl.innerHTML = `<div style="color:#ff4d4f;padding:12px;">图表渲染失败</div>`;
            }
          }
        } catch (error) {
          console.error('ECharts加载失败:', error);
        }
      };
      renderECharts();
    }

    // 渲染 Mermaid
    const mermaidElements = el.querySelectorAll('.mermaid-chart');
    if (mermaidElements.length > 0) {
      const renderMermaid = async () => {
        try {
          const mermaid = await loadMermaid();
          for (const mermaidEl of mermaidElements) {
            const encoded = mermaidEl.getAttribute('data-mermaid-source');
            if (!encoded) continue;

            let code;
            try { code = decodeURIComponent(escape(atob(encoded))); } catch (e) { try { code = atob(encoded); } catch(e2) { continue; } }
            if (!code || !code.trim()) continue;

            try {
              mermaid.parse(code.trim());

              const idx = mermaidEl.getAttribute('data-mermaid-idx') || Math.random().toString(36).substr(2, 9);
              const id = `mermaid-svg-${idx}-${Date.now()}`;
              const { svg } = await mermaid.render(id, code.trim());
              mermaidEl.innerHTML = svg;
            } catch (error) {
              try {
                document.querySelectorAll('[id^="dmermaid-svg-"]').forEach(e => e.remove());
              } catch(e) {}
              mermaidEl.innerHTML = `<div style="padding:12px;background:#f6f8fa;border-radius:6px;border:1px solid #e8e8e8;">
                <div style="font-size:12px;color:#999;margin-bottom:4px;">Mermaid 图表</div>
                <pre style="font-size:12px;color:#666;white-space:pre-wrap;margin:0;">${code}</pre>
              </div>`;
            }
          }
        } catch (error) {
          console.error('Mermaid加载失败:', error);
        }
      };
      renderMermaid();
    }

    lastFullContentRef.current = content;
  }, [renderedHtml, streaming, content]);

  // 组件卸载时清理：通过DOM元素获取实例并销毁，避免操作已被销毁的实例
  useEffect(() => {
    return () => {
      const el = containerRef.current;
      if (el && echartsInstance) {
        el.querySelectorAll('.echarts-chart').forEach(chartEl => {
          const chart = echartsInstance.getInstanceByDom(chartEl);
          if (chart && !chart._disposed) {
            try { chart.dispose(); } catch(e) {}
          }
        });
      }
      echartsInstancesRef.current = [];
    };
  }, []);

  // 窗口resize
  useEffect(() => {
    const handleResize = () => {
      echartsInstancesRef.current.forEach(chart => {
        if (chart && !chart._disposed) {
          try { chart.resize(); } catch(e) {}
        }
      });
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <>
      <div 
        ref={containerRef}
        className={`markdown-body${streaming ? ' streaming' : ''}`}
        onClick={handleImageClick}
        dangerouslySetInnerHTML={{ __html: renderedHtml }}
      />
      
      <Modal
        open={previewVisible}
        title={previewTitle}
        footer={null}
        onCancel={() => setPreviewVisible(false)}
        width="80%"
        centered
      >
        <img src={previewImage} alt={previewTitle} style={{ width: '100%', height: 'auto' }} />
      </Modal>
    </>
  );
}

export default MarkdownRenderer;