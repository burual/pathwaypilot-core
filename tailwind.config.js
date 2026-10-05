/** ============================================================
 *  PathwayPilot 原型 · Tailwind 配置 v0.2（学术紫 · 主色 #6E1F57）
 *  仅原型、不接真实数据。改色时同步 design-tokens.css 保持一致。
 *  content 只扫当前目录下的 *.html（原型为单页/多页静态 HTML）
 *  ============================================================ */
module.exports = {
  content: ['./*.html'],
  theme: {
    extend: {
      colors: {
        primary: {
          50:  '#F7F0F5',   // 卡片/侧栏/输入框背景
          100: '#EDE0E9',   // 分割线/表格边框/轻描边
          400: '#8A3A72',   // 次级强调：进度、选中态
          500: '#6E1F57',   // ★主色
          600: '#581845',   // 主按钮 hover/active、焦点边框
        },
        neutral: {
          100: '#F3F4F6',   // 页面背景
          300: '#D1D5DB',   // 边框/分隔线
          400: '#9CA3AF',   // 证据低·灰点
          500: '#6B7280',   // 禁用/占位
          700: '#3D4452',   // 次要文字
          900: '#1A1D23',   // 正文主文字
        },
        up:      '#C05621', // 上调/丰度增加
        down:    '#2C7A7B', // 下调/丰度减少
        warning: '#D69E2E',
        error:   '#C53030',
        success: '#276749',
        ev: {
          high: '#6E1F57',
          mid:  'rgba(110,31,87,.5)',
          low:  '#9CA3AF',
        },
        // 深色「学术模式」备用色（配 data-theme 使用）
        dark: { bg: '#111827', card: '#1F2937', text: '#E5E7EB', border: '#374151', accent: '#9B6A9E' },
      },

      fontFamily: {
        heading: ['Inter', 'Source Sans 3', 'Source Sans Pro', 'system-ui', 'sans-serif'],
        sans:    ['Inter', 'system-ui', 'sans-serif'],
        mono:    ['JetBrains Mono', 'Roboto Mono', 'ui-monospace', 'monospace'],
      },

      fontSize: {
        hero:  ['36px', { lineHeight: '44px' }],
        h1:    ['28px', { lineHeight: '36px' }],
        h2:    ['20px', { lineHeight: '28px' }],
        h3:    ['16px', { lineHeight: '24px', fontWeight: '700' }],
        body:  ['15px', { lineHeight: '24px' }],
        small: ['13px', { lineHeight: '20px' }],
        mono:  ['14px', { lineHeight: '22px' }],
      },

      spacing: {
        1: '4px', 2: '8px', 3: '12px', 4: '16px', 5: '20px',
        6: '24px', 8: '32px', 10: '40px', 12: '48px', 16: '64px',
      },

      borderRadius: {
        DEFAULT: '8px',   // 卡片
        sm: '4px',        // 标签/徽章
        tag: '4px',
        md: '6px',        // 按钮/输入框
        btn: '6px',
        input: '6px',
        card: '8px',
        lg: '12px',       // 模态框
        modal: '12px',
      },

      boxShadow: {
        card:  '0 1px 2px rgba(0,0,0,0.05)',
        modal: '0 4px 6px rgba(0,0,0,0.1)',
      },

      height: { navbar: '64px' },
    },
  },
};
