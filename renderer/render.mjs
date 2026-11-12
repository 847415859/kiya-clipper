#!/usr/bin/env node
// 成片渲染入口（worker/scripts/render.py 调用）：
//   node render.mjs <素材目录> <props.json> <输出.mp4>
// 素材目录里放 source.mp4、stickers/、fonts/；这里起一个只监听本机的 HTTP 服务把它们提供给 Remotion。
// 环境变量：
//   REMOTION_BROWSER      已安装的 Chrome / chrome-headless-shell 路径（国内下载不了 Remotion 自带浏览器时用）
//   REMOTION_CONCURRENCY  并发帧数，默认 1（2 核 2G 服务器别调高）
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import bundlerPkg from '@remotion/bundler';
import rendererPkg from '@remotion/renderer';

const { bundle } = bundlerPkg;
const { renderMedia, selectComposition, ensureBrowser } = rendererPkg;
const HERE = path.dirname(fileURLToPath(import.meta.url));

const TYPES = {
  '.mp4': 'video/mp4', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.webp': 'image/webp', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.ttf': 'font/ttf', '.otf': 'font/otf', '.ttc': 'font/collection', '.woff2': 'font/woff2'
};

function serve(root) {
  const base = path.resolve(root);
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const file = path.resolve(base, '.' + rel);
    if (!file.startsWith(base + path.sep)) { res.writeHead(403); return res.end(); }
    fs.stat(file, (err, st) => {
      if (err || !st.isFile()) { res.writeHead(404); return res.end(); }
      const headers = {
        'Content-Type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream',
        'Access-Control-Allow-Origin': '*', 'Accept-Ranges': 'bytes'
      };
      const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
      if (m) {
        const start = m[1] ? Number(m[1]) : 0;
        const end = m[2] ? Number(m[2]) : st.size - 1;
        res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${st.size}`, 'Content-Length': end - start + 1 });
        fs.createReadStream(file, { start, end }).pipe(res);
      } else {
        res.writeHead(200, { ...headers, 'Content-Length': st.size });
        fs.createReadStream(file).pipe(res);
      }
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ server, base: `http://127.0.0.1:${server.address().port}` })));
}

function newestMtime(dir) {
  let t = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    t = Math.max(t, e.isDirectory() ? newestMtime(p) : fs.statSync(p).mtimeMs);
  }
  return t;
}

// 打包 Remotion 项目（webpack，较慢）：源码没改就复用上次的结果
async function getBundle() {
  const out = path.join(HERE, 'build');
  const index = path.join(out, 'index.html');
  const src = Math.max(newestMtime(path.join(HERE, 'src')), fs.statSync(path.join(HERE, 'package.json')).mtimeMs);
  if (fs.existsSync(index) && fs.statSync(index).mtimeMs > src) return out;
  console.log('打包渲染模板（源码有改动时才需要，约 1 分钟）…');
  return bundle({ entryPoint: path.join(HERE, 'src', 'index.jsx'), outDir: out });
}

async function main() {
  const [assetDir, propsFile, output] = process.argv.slice(2);
  if (!assetDir || !propsFile || !output) {
    console.error('用法：node render.mjs <素材目录> <props.json> <输出.mp4>');
    process.exit(2);
  }
  const props = JSON.parse(fs.readFileSync(propsFile, 'utf8'));
  const { server, base } = await serve(assetDir);
  props.assetBase = base;
  const browserExecutable = process.env.REMOTION_BROWSER || null;
  try {
    if (!browserExecutable) await ensureBrowser();
    const serveUrl = await getBundle();
    const composition = await selectComposition({ serveUrl, id: 'Clip', inputProps: props, browserExecutable });
    const total = composition.durationInFrames;
    console.log(`开始渲染：${composition.width}x${composition.height}，${total} 帧（${(total / composition.fps).toFixed(1)} 秒）`);
    let last = -1;
    const t0 = Date.now();
    await renderMedia({
      composition, serveUrl, inputProps: props, codec: 'h264', outputLocation: output,
      browserExecutable,
      concurrency: Number(process.env.REMOTION_CONCURRENCY || 1),
      crf: 23, x264Preset: 'veryfast',
      offthreadVideoCacheSizeInBytes: 256 * 1024 * 1024,
      timeoutInMilliseconds: 120000,
      onProgress: ({ progress }) => {
        const p = Math.floor(progress * 20) * 5;
        if (p !== last) {
          last = p;
          const s = (Date.now() - t0) / 1000;
          const eta = progress > 0.02 ? (s / progress - s) : 0;
          console.log(`  ${p}%  已用 ${s.toFixed(0)} 秒${eta ? `，预计还要 ${eta.toFixed(0)} 秒` : ''}`);
        }
      }
    });
    console.log(`完成：${output}`);
  } finally {
    server.close();
  }
}

main().catch((e) => {
  console.error('渲染失败：' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
