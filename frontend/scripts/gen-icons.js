/**
 * 图标生成 —— 以项目根 icon.png 为唯一源
 *
 * 生成 frontend/resources/icon.ico (Windows) 和 icon.icns (macOS)，
 * 并把源 png 同步到 resources/icon.png，保证三件套一致。
 *
 * 源文件：../../icon.png (D:\coding\alphawise\icon.png)
 * 输出：  ../resources/icon.{ico,icns,png}
 *
 * 由 npm run gen-icons 调用，build / build:win / build:mac 前置执行。
 */
const fs = require('fs');
const path = require('path');
const png2icons = require('png2icons');

const SOURCE = path.join(__dirname, '..', '..', 'icon.png'); // 项目根
const OUT_DIR = path.join(__dirname, '..', 'resources');

if (!fs.existsSync(SOURCE)) {
    console.error(`[gen-icons] 源文件不存在: ${SOURCE}`);
    process.exit(1);
}

const png = fs.readFileSync(SOURCE);

// ICO: forWinExe=true 包含 256x256 大尺寸（Vista+ EXE 图标推荐）
const ico = png2icons.createICO(png, png2icons.BICUBIC, 0, false, true);
// ICNS: macOS app/dmg 图标
const icns = png2icons.createICNS(png, png2icons.BICUBIC, 0);

fs.writeFileSync(path.join(OUT_DIR, 'icon.ico'), ico);
fs.writeFileSync(path.join(OUT_DIR, 'icon.icns'), icns);
// 保持 resources/icon.png 与源一致（linux 图标 + 备份）
fs.copyFileSync(SOURCE, path.join(OUT_DIR, 'icon.png'));
// 同步到 frontend/icon.png：main.js 窗口图标读 __dirname/icon.png，
// 该文件被 package.json files 打包进 app.asar，供生产模式窗口/任务栏图标使用
fs.copyFileSync(SOURCE, path.join(__dirname, '..', 'icon.png'));

console.log('[gen-icons] 已生成 icon.ico / icon.icns / icon.png (+ frontend/icon.png)');
