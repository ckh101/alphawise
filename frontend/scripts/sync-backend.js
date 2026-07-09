/**
 * 同步 backend/ 的源码到 frontend/build-backend/（打包源）
 *
 * 为什么需要这个脚本：
 *   electron-builder 的 extraResources 把 build-backend/ 原样塞进安装包，
 *   但 build-backend/ 不是 backend/ 的镜像 —— 它还含嵌入式运行时（python/、node/）
 *   和生产依赖。项目没有自动同步机制，改 backend/ 后必须手动同步，
 *   否则打包出来的是旧代码。
 *
 * 策略：白名单同步 + 排除运行产物
 *   - 只同步源码目录/文件（白名单），python/ node/ data/ 自动保留不覆盖
 *   - 排除 __pycache__、.venv、logs、运行产物（out1~4、mx_output.txt 等）
 *   - 不删 build-backend/ 里白名单之外的文件，避免误删运行时
 */

const path = require('path');
const fs = require('fs');

const SRC = path.resolve(__dirname, '..', '..', 'backend');
const DST = path.resolve(__dirname, '..', 'build-backend');

// 同步白名单（顶层路径）：只同步源码/配置/技能
const SYNC_DIRS = ['.claude', 'harness', 'worker', 'skills', 'resources'];
const SYNC_FILES = ['worker_main.py', 'requirements-prod.txt'];

// 拷贝时排除的模式（运行产物、缓存、临时文件、数据库、日志）
// 注意：不能全局排 *.txt —— news-search/scripts/requirements.txt 和 test_queries.txt 是源码
const EXCLUDE = [
  /(^|[\\\/])__pycache__([\\\/]|$)/,
  /(^|[\\\/])__MACOSX([\\\/]|$)/, // mac 解压残留
  /(^|[\\\/])\.venv([\\\/]|$)/,
  /(^|[\\\/])\.pytest_cache([\\\/]|$)/,
  /(^|[\\\/])harness\.egg-info([\\\/]|$)/,
  /\.pyc$/,
  /(^|[\\\/])logs([\\\/]|$)/,
  /(^|[\\\/])data([\\\/]|$)/, // 数据库不进包（运行时生成）
  // skill 运行产物目录：out1~4、中文目录名（"今日A股..."、"2026年5月17日..."）
  /(^|[\\\/])out\d*([\\\/]|$)/,
  /(^|[\\\/])[^\\\/]*[一-龥][^\\\/]*$/, // 路径段含中文（产物目录/文件）
  // skill 运行产物文件
  /^mx_output\.txt$/,
  /^mx_result\.txt$/,
  /^mx_xuangu_output\.txt$/,
  /^output.*\.txt$/, // output.txt / output2.txt / output_today.txt / output_quote.txt ...
  /^output_err.*\.txt$/,
  /^output_raw.*\.txt$/,
  /^output_result\.txt$/,
  /^result\.txt$/,
  /^out\d*\.txt$/, // out1.txt
  /^_meta\.json$/,
  /^\..*$/, // 隐藏文件（.DS_Store 等）
];

function shouldExclude(relPath, name) {
  // 用 name 判断（白名单顶层目录如 .claude 不应被隐藏文件规则误伤）
  return EXCLUDE.some((re) => re.test(name));
}

function walk(srcDir, dstDir, relBase = '') {
  let count = 0;
  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    const name = entry.name;
    const rel = path.join(relBase, name);
    if (shouldExclude(rel, name)) continue;

    const src = path.join(srcDir, name);
    const dst = path.join(dstDir, name);

    if (entry.isDirectory()) {
      fs.mkdirSync(dst, { recursive: true });
      count += walk(src, dst, rel);
    } else {
      fs.copyFileSync(src, dst);
      count++;
    }
  }
  return count;
}

// 清空目录（递归删除内部所有内容，保留目录本身）
function cleanDir(dir) {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      fs.rmSync(p, { recursive: true, force: true });
    } else {
      fs.rmSync(p, { force: true });
    }
  }
}

if (!fs.existsSync(SRC)) {
  console.error(`[sync-backend] 源目录不存在: ${SRC}`);
  process.exit(1);
}
fs.mkdirSync(DST, { recursive: true });

let total = 0;
for (const dir of SYNC_DIRS) {
  const src = path.join(SRC, dir);
  if (!fs.existsSync(src)) continue;
  const dst = path.join(DST, dir);
  // 先清空目标目录（白名单目录都是纯源码，可安全清空；运行时在 python/ node/，不在白名单内）
  cleanDir(dst);
  fs.mkdirSync(dst, { recursive: true });
  const n = walk(src, dst, dir);
  console.log(`[sync-backend] ${dir}/  ->  ${n} files`);
  total += n;
}
for (const file of SYNC_FILES) {
  const src = path.join(SRC, file);
  if (!fs.existsSync(src)) continue;
  fs.copyFileSync(src, path.join(DST, file));
  console.log(`[sync-backend] ${file}`);
  total++;
}
console.log(`[sync-backend] done, ${total} files synced`);
