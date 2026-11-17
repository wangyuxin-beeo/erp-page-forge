// DSH browser module: the host supplies React and the native slot/layout services.
window.__ModuleLoader__.load({
  id: 'dsh-erp-page-forge',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const PANEL_ID = 'erp-page-forge'

    function PageIcon({ size = 20 }) {
      return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
        stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round',
        'aria-hidden': true },
        h('rect', { x: 3, y: 3, width: 18, height: 18, rx: 4 }),
        h('path', { d: 'M3 9h18M9 9v12M6 6h.01M9 6h.01M12 13h6M12 17h4' }))
    }

    function Workbench() {
      const [revision, setRevision] = React.useState(0)
      const [loading, setLoading] = React.useState(true)
      const [status, setStatus] = React.useState('正在连接…')
      return h('section', { 'aria-label': '页归工作台', style: {
        display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, minWidth: 0,
        background: 'var(--dsw-alias-bg-base)', color: 'var(--dsw-alias-label-primary)',
      } },
        h('header', { style: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px',
          borderBottom: '1px solid var(--dsw-alias-border-l3)', flexShrink: 0 } },
          h(PageIcon, { size: 20 }),
          h('strong', null, '页归'),
          h('span', { role: 'status', style: { flex: 1, fontSize: 12, opacity: .6 } },
            loading ? '正在加载工作台…' : status),
          h('button', { type: 'button', onClick: () => { setLoading(true); setRevision(value => value + 1) },
            style: { color: 'inherit', background: 'transparent', border: '1px solid var(--dsw-alias-border-l3)',
              borderRadius: 6, padding: '5px 10px', cursor: 'pointer' } }, '重新加载')),
        h('iframe', { key: revision, title: '页归 ERP 页面工作台', src: '/erp-page-forge/?embedded=1.8.0&reload=' + revision,
          onLoad: async event => {
            setLoading(false)
            try {
              const response = await fetch('/erp-page-forge/api/templates', { cache: 'no-store' })
              if (!response.ok) throw new Error('HTTP ' + response.status)
              const data = await response.json()
              setStatus('1.8.0 · 已连接 · ' + (data.templates || []).length + ' 个模板')
            } catch (error) { setStatus('1.8.0 · 连接失败：' + error.message) }
          },
          style: { border: 0, width: '100%', flex: 1, minHeight: 0, background: '#f5f7fa' } }))
    }

    return {
      inject: ['slots', 'layout'],
      apply(ctx) {
        ctx.effect(() => ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main', key: PANEL_ID, inject: () => ({}),
        }, Workbench)))
        ctx.effect(() => ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist', id: PANEL_ID, order: -5, label: () => '页归',
        }, PageIcon)))
      },
    }
  },
})
