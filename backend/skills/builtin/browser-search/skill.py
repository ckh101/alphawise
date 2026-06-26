"""
Browser Search Skill Implementation
基于 Playwright 的浏览器自动化搜索技能
通过无头浏览器访问 Bing 搜索引擎，抓取实时新闻和内容

搜索链路: Bing 中国版 → Bing 国际版（回退）
百度搜索对 headless 浏览器有严格反爬限制（图形验证码），不使用。

依赖: playwright (可选)
安装: pip install playwright && playwright install chromium
"""

from typing import Any
from urllib.parse import quote

from harness.core.exceptions import SkillError
from harness.core.logger import get_logger

logger = get_logger(__name__)


class BrowserSearchSkill:
    """
    浏览器自动化搜索技能类

    使用 Playwright 启动无头 Chromium 浏览器，
    访问 Bing 搜索引擎抓取实时搜索结果。
    在 web-search API 不可用或结果不足时作为增强/回退方案。
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self.name = "browser-search"
        self.description = "浏览器自动化搜索技能，通过 Playwright 抓取 Bing 搜索引擎实时结果"
        logger.info("BrowserSearchSkill initialized")

    async def _ensure_browser(self):
        """懒加载：首次调用时启动浏览器"""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise SkillError(
                    "playwright 未安装。请运行: pip install playwright && playwright install chromium",
                    error_code="BS_001"
                )

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            logger.info("Headless Chromium browser launched")

    async def _scrape_bing(self, query: str, count: int = 10) -> list[dict]:
        """
        访问 Bing 搜索并解析结果列表

        搜索链路: Bing 中国版(cn.bing.com) → Bing 国际版(bing.com 回退)

        Args:
            query: 搜索关键词
            count: 最大返回结果数

        Returns:
            搜索结果列表
        """
        # 尝试 Bing 中国版，失败则回退国际版
        for base_url in ["https://cn.bing.com", "https://www.bing.com"]:
            items = await self._do_bing_scrape(base_url, query, count)
            if items:
                return items
            logger.warning(f"No results from {base_url}, trying next search engine")

        return []

    async def _do_bing_scrape(self, base_url: str, query: str, count: int) -> list[dict]:
        """执行单次 Bing 搜索并解析"""
        page = await self._browser.new_page()
        try:
            page.set_default_timeout(15000)

            encoded_query = quote(query)
            url = f"{base_url}/search?q={encoded_query}&count={min(count, 20)}"

            logger.info(f"Browser searching: {url}")
            await page.goto(url, wait_until="domcontentloaded")

            # 等待搜索结果加载
            try:
                await page.wait_for_selector(".b_algo", timeout=10000)
            except Exception:
                logger.warning("Bing .b_algo selector not found, trying alternative extraction")

            # 提取搜索结果
            results = await page.evaluate("""(maxCount) => {
                const items = [];
                document.querySelectorAll('.b_algo').forEach((el, i) => {
                    if (i >= maxCount) return;

                    const titleEl = el.querySelector('h2 a');
                    const contentEl = el.querySelector('.b_caption p') ||
                                      el.querySelector('.b_caption') ||
                                      el.querySelector('p');
                    const sourceEl = el.querySelector('.b_caption .news-card-source') ||
                                     el.querySelector('.b_attribution cite') ||
                                     el.querySelector('cite');

                    if (titleEl) {
                        items.push({
                            title: titleEl.textContent?.trim() || '',
                            url: titleEl.href || '',
                            content: contentEl?.textContent?.trim() || '',
                            source: sourceEl?.textContent?.trim() || '',
                            date: ''
                        });
                    }
                });

                // 如果 .b_algo 没有结果，尝试其他选择器（Bing 页面结构可能变化）
                if (items.length === 0) {
                    document.querySelectorAll('#b_results > li').forEach((el, i) => {
                        if (i >= maxCount) return;
                        const titleEl = el.querySelector('h2 a');
                        const contentEl = el.querySelector('p');
                        if (titleEl) {
                            items.push({
                                title: titleEl.textContent?.trim() || '',
                                url: titleEl.href || '',
                                content: contentEl?.textContent?.trim() || '',
                                source: '',
                                date: ''
                            });
                        }
                    });
                }
                return items;
            }""", count)

            # 过滤空结果，清理特殊 Unicode 字符
            cleaned = []
            for r in results:
                if r.get("title"):
                    for key in ("title", "content", "source"):
                        val = r.get(key, "")
                        if val:
                            r[key] = val.replace("\u2002", " ").replace("\u2003", " ").replace("\u00a0", " ").strip()
                    cleaned.append(r)
            logger.info(f"Bing scrape from {base_url}: {len(cleaned)} results for '{query}'")
            return cleaned

        except Exception as e:
            logger.error(f"Bing scrape error ({base_url}): {e}")
            return []
        finally:
            await page.close()

    async def search(self, query: str, count: int = 10, **kwargs) -> dict[str, Any]:
        """
        执行浏览器搜索

        Args:
            query: 搜索关键词
            count: 返回结果数量

        Returns:
            搜索结果字典，结构与 web-search 兼容
            {items: [...], summary: str, total: int}
        """
        logger.info(f"Browser search: query='{query}', count={count}")

        await self._ensure_browser()

        try:
            items = await self._scrape_bing(query, count)

            summary_parts = []
            for item in items:
                if item.get("content"):
                    summary_parts.append(f"- {item['title']}: {item['content'][:200]}")

            return {
                "items": items,
                "summary": "\n".join(summary_parts) if summary_parts else "未找到相关结果",
                "total": len(items)
            }

        except Exception as e:
            logger.error(f"Browser search failed: {e}")
            return {
                "items": [],
                "summary": f"浏览器搜索失败: {str(e)}",
                "total": 0,
                "error": str(e)
            }

    async def search_stock_news(self, symbol: str, stock_name: str = "") -> dict[str, Any]:
        """
        搜索股票相关新闻（投研专用接口）

        Args:
            symbol: 股票代码，如 "600519.SH"
            stock_name: 股票名称，如 "贵州茅台"

        Returns:
            包含多个维度搜索结果的综合字典
        """
        code = symbol.split(".")[0] if "." in symbol else symbol
        name = stock_name or code

        logger.info(f"Browser stock news search: {name} ({code})")

        # 多维度搜索（与 web-search 保持一致）
        queries = [
            f"{name} {code} 最新消息 股票",
            f"{name} 财报 业绩 最新",
        ]

        all_items = []
        summaries = []

        for query in queries:
            result = await self.search(query=query, count=8)
            if result.get("items"):
                all_items.extend(result["items"])
            if result.get("summary"):
                summaries.append(f"**{query}**:\n{result['summary']}")

        # URL 去重
        seen_urls = set()
        unique_items = []
        for item in all_items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_items.append(item)
            elif not url:
                unique_items.append(item)

        return {
            "symbol": symbol,
            "stock_name": name,
            "items": unique_items[:15],
            "summary": "\n\n".join(summaries) if summaries else "未找到相关新闻",
            "total": len(unique_items[:15])
        }

    async def close(self):
        """关闭浏览器，释放资源"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser closed")

    def connect(self) -> None:
        """同步连接接口（兼容 skill 注册规范，实际为懒加载）"""
        logger.info("BrowserSearchSkill connect (lazy - browser launches on first search)")

    async def disconnect(self) -> None:
        """断开连接"""
        await self.close()

    def search_sync(self, query: str, count: int = 10) -> dict[str, Any]:
        """
        同步版本的搜索方法，使用同步 Playwright API。
        用于在 uvicorn 等已有事件循环环境中通过 asyncio.to_thread 调用。
        """
        from playwright.sync_api import sync_playwright

        logger.info(f"Browser search (sync): query='{query}', count={count}")

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            try:
                # 尝试 Bing 中国版 → 国际版
                items = []
                for base_url in ["https://cn.bing.com", "https://www.bing.com"]:
                    items = self._do_bing_scrape_sync(browser, base_url, query, count)
                    if items:
                        break
                    logger.warning(f"No results from {base_url} (sync), trying next")

                summary_parts = []
                for item in items:
                    if item.get("content"):
                        summary_parts.append(f"- {item['title']}: {item['content'][:200]}")

                return {
                    "items": items,
                    "summary": "\n".join(summary_parts) if summary_parts else "未找到相关结果",
                    "total": len(items)
                }
            finally:
                browser.close()
                pw.stop()

        except Exception as e:
            logger.error(f"Browser search sync failed: {e}")
            return {"items": [], "summary": f"浏览器搜索失败: {str(e)}", "total": 0, "error": str(e)}

    def _do_bing_scrape_sync(self, browser, base_url: str, query: str, count: int) -> list[dict]:
        """同步版单次 Bing 搜索"""
        page = browser.new_page()
        try:
            page.set_default_timeout(15000)

            encoded_query = quote(query)
            url = f"{base_url}/search?q={encoded_query}&count={min(count, 20)}"

            logger.info(f"Browser searching (sync): {url}")
            page.goto(url, wait_until="domcontentloaded")

            try:
                page.wait_for_selector(".b_algo", timeout=10000)
            except Exception:
                logger.warning("Bing .b_algo selector not found (sync)")

            results = page.evaluate("""(maxCount) => {
                const items = [];
                document.querySelectorAll('.b_algo').forEach((el, i) => {
                    if (i >= maxCount) return;
                    const titleEl = el.querySelector('h2 a');
                    const contentEl = el.querySelector('.b_caption p') || el.querySelector('.b_caption') || el.querySelector('p');
                    const sourceEl = el.querySelector('.b_caption .news-card-source') || el.querySelector('.b_attribution cite') || el.querySelector('cite');
                    if (titleEl) {
                        items.push({
                            title: (titleEl.textContent || '').trim(),
                            url: titleEl.href || '',
                            content: (contentEl?.textContent || '').trim().replace(/\\u2002/g, ' ').replace(/\\u2003/g, ' ').replace(/\\u00a0/g, ' '),
                            source: (sourceEl?.textContent || '').trim(),
                            date: ''
                        });
                    }
                });
                if (items.length === 0) {
                    document.querySelectorAll('#b_results > li').forEach((el, i) => {
                        if (i >= maxCount) return;
                        const titleEl = el.querySelector('h2 a');
                        const contentEl = el.querySelector('p');
                        if (titleEl) {
                            items.push({
                                title: (titleEl.textContent || '').trim(),
                                url: titleEl.href || '',
                                content: (contentEl?.textContent || '').trim(),
                                source: '',
                                date: ''
                            });
                        }
                    });
                }
                return items;
            }""", count)

            items = [r for r in results if r.get("title")]
            logger.info(f"Bing sync scrape from {base_url}: {len(items)} results for '{query}'")
            return items

        except Exception as e:
            logger.error(f"Bing sync scrape error ({base_url}): {e}")
            return []
        finally:
            page.close()

    def __enter__(self):
        self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
