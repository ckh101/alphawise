/**
 * 预加载脚本
 *
 * 在渲染进程中暴露安全的API
 */

const { contextBridge, ipcRenderer } = require('electron');

/**
 * 暴露给渲染进程的安全API
 */
contextBridge.exposeInMainWorld('electronAPI', {
    /**
     * Ping测试
     */
    ping: async () => {
        const startTime = Date.now();
        const result = await ipcRenderer.invoke('ping');
        const duration = Date.now() - startTime;
        console.log(`[preload] Ping completed in ${duration}ms`, result);
        return result;
    },

    /**
     * 获取应用信息
     */
    getAppInfo: async () => {
        const result = await ipcRenderer.invoke('get-app-info');
        console.log('[preload] App info:', result);
        return result;
    },

    /**
     * 窗口控制
     */
    windowMinimize: () => ipcRenderer.invoke('window-minimize'),
    windowMaximize: () => ipcRenderer.invoke('window-maximize'),
    windowClose: () => ipcRenderer.invoke('window-close'),
    windowIsMaximized: () => ipcRenderer.invoke('window-is-maximized'),

    /**
     * 确认弹窗（显示应用名称而非 harness-front）
     */
    confirm: async (message) => {
        return await ipcRenderer.invoke('dialog-confirm', message);
    },

    /**
     * 监听事件
     */
    on: (channel, callback) => {
        console.log(`[preload] Setting up listener for ${channel}`);
        ipcRenderer.on(channel, (event, ...args) => callback(...args));
    },

    /**
     * 监听后端就绪事件
     */
    onBackendReady: (callback) => {
        ipcRenderer.on('backend-ready', () => callback());
    },

    /**
     * 移除监听器
     */
    removeListener: (channel, callback) => {
        console.log(`[preload] Removing listener for ${channel}`);
        ipcRenderer.removeListener(channel, callback);
    },

    /**
     * 选择文件（用于 skill zip 上传等）
     */
    selectFile: async (options) => {
        return await ipcRenderer.invoke('select-file', options);
    },

    /**
     * 读取文件为 Base64（用于上传）
     */
    readFileBase64: async (filePath) => {
        return await ipcRenderer.invoke('read-file-base64', filePath);
    },

    /**
     * 生成 PDF（Electron 内置 Chromium 渲染）
     */
    generatePDF: async (html) => {
        return await ipcRenderer.invoke('generate-pdf', html);
    },

    /**
     * 打开日志目录（调用系统文件管理器）
     */
    openLogDir: () => ipcRenderer.invoke('open-log-dir'),
});

console.log('[preload] Preload script loaded');
