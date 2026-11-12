// 吐槽旁白（narration）和伪 UI（fakeui）的样式：纯函数，返回“样式树” { style, text, children }。
// 成片渲染（React）和后台编辑器预览（Vue）都用它画，保证所见即所得。尺寸以 1080p 为基准按画面高度缩放。
// 伪 UI 都是通用的假界面（系统弹窗 / 聊天消息 / 成就 / 任务通知），不仿冒任何真实平台或软件。

const FONT = '"Noto Sans CJK SC", "Microsoft YaHei", sans-serif';
const hue = (s) => [...(s || '?')].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 7);

// a = enterExit 的结果 { scale, opacity, dx, dy, rotate }；不传就是静止状态
function place(item, W, H, a) {
  a = a || { scale: 1, opacity: 1, dx: 0, dy: 0, rotate: 0 };
  return {
    position: 'absolute',
    left: item.x * W,
    top: item.y * H,
    transform: `translate(-50%, -50%) translate(${a.dx}px, ${a.dy}px) rotate(${a.rotate}deg) scale(${a.scale})`,
    opacity: a.opacity,
    fontFamily: FONT
  };
}

export function narrationView(item, W, H, a) {
  const k = H / 1080;
  const size = (item.size || 54) * k;
  const bubble = item.style === 'bubble';
  const box = bubble
    ? { background: '#ffffff', color: '#1b1b22', border: `${4 * k}px solid #1b1b22`, borderRadius: size * 0.6, boxShadow: `${6 * k}px ${6 * k}px 0 rgba(0,0,0,0.35)` }
    : { background: 'rgba(18,18,26,0.82)', color: '#ffffff', borderRadius: size * 0.3, boxShadow: `0 ${6 * k}px ${18 * k}px rgba(0,0,0,0.4)` };
  const children = [];
  if (item.tag) {
    children.push({
      style: { background: item.color || '#FF6B8B', color: '#ffffff', fontSize: size * 0.55, fontWeight: 900, padding: `${size * 0.1}px ${size * 0.3}px`,
               borderRadius: size * 0.2, whiteSpace: 'nowrap', flex: 'none' },
      text: item.tag
    });
  }
  children.push({ style: { fontSize: size, fontWeight: 800, lineHeight: 1.25, whiteSpace: 'pre-wrap', minWidth: 0 }, text: item.text });
  return {
    style: { ...place(item, W, H, a), ...box, display: 'flex', alignItems: 'center', gap: 14 * k, padding: `${size * 0.28}px ${size * 0.5}px`, maxWidth: W * 0.86, width: 'max-content' },
    children
  };
}

export function fakeUiView(item, W, H, a) {
  const k = H / 1080;
  const width = item.scale * W;
  const root = { ...place(item, W, H, a), width };
  const text = item.text;
  const title = item.title || '';
  switch (item.kind) {
    case 'chat':
      return {
        style: { ...root, display: 'flex', alignItems: 'flex-start', gap: 14 * k, background: 'rgba(255,255,255,0.95)', borderRadius: 18 * k, padding: 14 * k, boxShadow: `0 ${8 * k}px ${24 * k}px rgba(0,0,0,0.35)` },
        children: [
          { style: { width: 64 * k, height: 64 * k, borderRadius: '50%', background: `hsl(${hue(title)}, 65%, 55%)`, color: '#fff', fontSize: 32 * k, fontWeight: 800,
                     display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }, text: [...(title || '路')][0] },
          { style: { display: 'flex', flexDirection: 'column', gap: 4 * k, minWidth: 0 }, children: [
            { style: { fontSize: 24 * k, color: '#6b7280', fontWeight: 700 }, text: title },
            { style: { fontSize: 34 * k, color: '#111827', fontWeight: 700, lineHeight: 1.3, whiteSpace: 'pre-wrap' }, text }
          ] }
        ]
      };
    case 'achievement':
      return {
        style: { ...root, display: 'flex', alignItems: 'center', gap: 18 * k, background: 'linear-gradient(135deg, #2c2c34, #14141a)', border: `${3 * k}px solid #d9a93a`,
                 borderRadius: 14 * k, padding: `${14 * k}px ${22 * k}px`, color: '#fff', boxShadow: `0 ${8 * k}px ${24 * k}px rgba(0,0,0,0.5)` },
        children: [
          { style: { fontSize: 64 * k, color: '#f5c542', lineHeight: 1, flex: 'none' }, text: '★' },
          { style: { display: 'flex', flexDirection: 'column', gap: 2 * k, minWidth: 0 }, children: [
            { style: { fontSize: 22 * k, color: '#f5c542', fontWeight: 800, letterSpacing: 2 * k }, text: title },
            { style: { fontSize: 34 * k, fontWeight: 800, lineHeight: 1.25, whiteSpace: 'pre-wrap' }, text }
          ] }
        ]
      };
    case 'notice':
      return {
        style: { ...root, background: 'rgba(24,26,32,0.86)', borderLeft: `${8 * k}px solid #4caf50`, borderRadius: `0 ${10 * k}px ${10 * k}px 0`,
                 padding: `${12 * k}px ${22 * k}px`, color: '#fff', boxShadow: `0 ${6 * k}px ${18 * k}px rgba(0,0,0,0.4)` },
        children: [
          { style: { fontSize: 22 * k, color: '#7bd88f', fontWeight: 800 }, text: title },
          { style: { fontSize: 32 * k, fontWeight: 700, lineHeight: 1.3, whiteSpace: 'pre-wrap' }, text }
        ]
      };
    default: // popup：系统弹窗
      return {
        style: { ...root, background: '#f2f2f6', border: `${2 * k}px solid #7b8794`, borderRadius: 10 * k, overflow: 'hidden', boxShadow: `0 ${10 * k}px ${30 * k}px rgba(0,0,0,0.45)` },
        children: [
          { style: { background: 'linear-gradient(#4a79b8, #2f5f9e)', color: '#fff', fontSize: 28 * k, fontWeight: 700, padding: `${10 * k}px ${16 * k}px` }, text: title },
          { style: { color: '#222', fontSize: 34 * k, fontWeight: 700, padding: `${24 * k}px ${20 * k}px ${14 * k}px`, lineHeight: 1.35, whiteSpace: 'pre-wrap' }, text },
          { style: { display: 'flex', justifyContent: 'flex-end', padding: `0 ${16 * k}px ${16 * k}px` }, children: [
            { style: { background: '#e6e8ee', border: `${2 * k}px solid #9aa3b0`, borderRadius: 6 * k, padding: `${6 * k}px ${26 * k}px`, fontSize: 26 * k, color: '#222' }, text: '确定' }
          ] }
        ]
      };
  }
}
