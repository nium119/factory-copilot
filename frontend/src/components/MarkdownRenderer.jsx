import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Modal } from 'antd';
import { marked } from 'marked';

// ECharts 加载 — 动态 import 在 Vite production 下可能失败，加 CDN fallback
let echartsLoadPromise = null;
function loadECharts() {
  if (echartsLoadPromise) return echartsLoadPromise;
  echartsLoadPromise = (async () => {
    try {
      const mod = await import('echarts');
      return mod;
    } catch (err) {
      console.warn('ECharts动态import失败，降级CDN:', err.message);
      // CDN fallback
      return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js';
        script.onload = () => resolve(window.echarts);
        script.onerror = () => { echartsLoadPromise = null; reject(new Error('CDN加载失败')); };
        document.head.appendChild(script);
      });
    }
  })();
  return echartsLoadPromise;
}

// Mermaid 懒加载
let mermaidLoadPromise = null;
function loadMermaid() {
  if (mermaidLoadPromise) return mermaidLoadPromise;
  mermaidLoadPromise = (async () => {
    const mod = await import('mermaid');
    mod.default.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose', fontFamily: 'inherit', logLevel: 'error', suppressErrorRendering: true });
    return mod.default;
  })().catch(err => {
    console.error('Mermaid加载失败:', err);
    mermaidLoadPromise = null;
    throw err;
  });
  return mermaidLoadPromise;
}

/**
 * 自定义 marked renderer
 * 将 ```echarts 和 ```mermaid 代码块替换为容器 div
 */
function createRenderer() {
  const renderer = new marked.Renderer();
  const originalCode = renderer.code.bind(renderer);

  renderer.code = function(code, language, isEscaped) {
    if (language === 'echarts') {
      const encoded = btoa(unescape(encodeURIComponent(code)));
      return `<div class="chart-block chart-placeholder-loading" data-chart-type="echarts" data-chart-src="${encoded}">图表正在生成中...</div>`;
    }
    if (language === 'mermaid') {
      const encoded = btoa(unescape(encodeURIComponent(code)));
      return `<div class="chart-block chart-placeholder-loading" data-chart-type="mermaid" data-chart-src="${encoded}">流程图正在生成中...</div>`;
    }
    return originalCode(code, language, isEscaped);
  };

  return renderer;
}

/**
 * 渲染 markdown 为 HTML
 * 统一使用 marked.parse + 自定义 renderer，无流式/完成分支
 */
function renderMarkdown(text) {
  if (!text) return '';
  const renderer = createRenderer();
  let html = marked.parse(text, { renderer, breaks: true, gfm: true });
  // 图片懒加载
  html = html.replace(/<img /g, '<img class="markdown-image" loading="lazy" ');
  return html;
}

/**
 * 初始化图表
 * 找到所有 .chart-block 容器，根据 data-chart-type 渲染 ECharts 或 Mermaid
 */
function initCharts(el, chartInstancesRef) {
  if (!el) return;
  const blocks = el.querySelectorAll('.chart-block');
  if (!blocks.length) return;

  // 销毁旧实例
  chartInstancesRef.current.forEach(instance => {
    if (instance && !instance._disposed) {
      try { instance.dispose(); } catch(e) {}
    }
  });
  chartInstancesRef.current = [];

  blocks.forEach(block => {
    const type = block.getAttribute('data-chart-type');
    const encoded = block.getAttribute('data-chart-src');
    if (!encoded) return;

    try {
      const source = decodeURIComponent(escape(atob(encoded)));

      if (type === 'echarts') {
        loadECharts().then(echarts => {
          if (echarts.getInstanceByDom(block)) return;
          const option = JSON.parse(source);
          block.classList.remove('chart-loading');
          block.innerHTML = '';
          const chart = echarts.init(block);
          chart.setOption(option);
          chartInstancesRef.current.push(chart);
        }).catch(() => {
          block.innerHTML = '<div style="color:#ff4d4f;padding:12px;">ECharts加载失败</div>';
        });
      } else if (type === 'mermaid') {
        loadMermaid().then(async (mermaid) => {
          if (block.querySelector('svg')) return;
          const id = `mermaid-svg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
          const { svg } = await mermaid.render(id, source.trim());
          block.classList.remove('chart-loading');
          block.innerHTML = svg;
        }).catch(() => {
          block.innerHTML = `<div style="padding:12px;background:#f6f8fa;border-radius:6px;border:1px solid #e8e8e8;">
            <div style="font-size:12px;color:#999;margin-bottom:4px;">Mermaid 加载失败</div>
            <pre style="font-size:12px;color:#666;white-space:pre-wrap;margin:0;">${source}</pre>
          </div>`;
        });
      }
    } catch (e) {
      block.innerHTML = `<div style="color:#ff4d4f;padding:12px;">图表解析失败</div>`;
    }
  });
}

function MarkdownRenderer({ content, streaming = false }) {
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewImage, setPreviewImage] = useState('');
  const [previewTitle, setPreviewTitle] = useState('');
  const containerRef = useRef(null);
  const chartInstancesRef = useRef([]);

  const renderedHtml = renderMarkdown(content);

  const handleImageClick = useCallback((e) => {
    if (e.target.tagName === 'IMG' && e.target.classList.contains('markdown-image')) {
      e.preventDefault();
      setPreviewImage(e.target.src);
      setPreviewTitle(e.target.alt || '');
      setPreviewVisible(true);
    }
  }, []);

  // 非流式时初始化图表（流式过程中内容不完整，等结束后再渲染）
  useEffect(() => {
    if (streaming) return;
    const el = containerRef.current;
    if (!el) return;

    const timer = setTimeout(() => initCharts(el, chartInstancesRef), 200);
    return () => clearTimeout(timer);
  }, [content, streaming]);

  // 窗口 resize
  useEffect(() => {
    const handleResize = () => {
      chartInstancesRef.current.forEach(chart => {
        if (chart && !chart._disposed) {
          try { chart.resize(); } catch(e) {}
        }
      });
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 组件卸载清理
  useEffect(() => {
    return () => {
      chartInstancesRef.current.forEach(chart => {
        if (chart && !chart._disposed) {
          try { chart.dispose(); } catch(e) {}
        }
      });
      chartInstancesRef.current = [];
    };
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
