import React from 'react';

// 把 fakeui.js 的样式树画成 DOM
export const FxNode = ({ node }) => (
  <div style={node.style}>
    {node.text}
    {(node.children || []).map((c, i) => <FxNode key={i} node={c} />)}
  </div>
);
