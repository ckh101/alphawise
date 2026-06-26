"""
Web Search Skill Implementation
基于智谱AI zai-sdk实现的联网搜索技能
用于获取股票相关新闻、公告和市场舆情数据
"""

from typing import Any

from harness.core.config import get_config
from harness.core.exceptions import SkillError
from harness.core.logger import get_logger

logger = get_logger(__name__)


class WebSearchSkill:
    """
    智谱联网搜索技能类

    使用 zai-sdk 的 ZhipuAiClient 调用智谱AI网络搜索API，
    为投研分析提供实时新闻、公告和市场舆情数据。
    """

    def __init__(self):
        self._client = None
        self.description = "智谱联网搜索技能，获取股票相关新闻、公告和市场舆情"
        self.name = "web-search"
        logger.info("WebSearchSkill initialized")

    def _get_client(self):
        """获取或创建 ZhipuAiClient 实例"""
        if self._client is None:
            try:
                from zai import ZhipuAiClient

                config = get_config()
                api_key = config.glm.api_key if hasattr(config, 'glm') else None
                if not api_key:
                    raise SkillError(
                        "GLM API key not configured",
                        error_code="WS_001"
                    )

                self._client = ZhipuAiClient(api_key=api_key)
                logger.info("ZhipuAiClient created for web search")
            except ImportError:
                raise SkillError(
                    "zai-sdk not installed. Run: pip install zai-sdk",
                    error_code="WS_002"
                )
        return self._client

    def get_tool_definition(self) -> dict[str, Any]:
        """获取工具定义"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    },
                    "count": {
                        "type": "integer",
                        "description": "返回结果数量(1-50)，默认10"
                    },
                    "recency_filter": {
                        "type": "string",
                        "description": "时间范围过滤: noLimit/oneDay/oneWeek/oneMonth/oneYear",
                        "default": "noLimit"
                    }
                },
                "required": ["query"]
            }
        }

    def search(self, query: str, count: int = 10, recency_filter: str = "noLimit") -> dict[str, Any]:
        """
        执行网络搜索

        Args:
            query: 搜索关键词
            count: 返回结果数量(1-50)
            recency_filter: 时间范围过滤

        Returns:
            搜索结果字典，包含新闻列表和摘要
        """
        logger.info(f"Web search: query='{query}', count={count}, recency={recency_filter}")

        client = self._get_client()

        try:
            response = client.web_search.web_search(
                search_engine="search_pro",
                search_query=query,
                count=min(max(count, 1), 50),
                search_recency_filter=recency_filter,
                content_size="high"
            )

            results = self._parse_response(response)

            logger.info(f"Web search returned {len(results.get('items', []))} results")
            return results

        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return {
                "query": query,
                "items": [],
                "summary": f"搜索失败: {str(e)}",
                "error": str(e)
            }

    def _parse_response(self, response) -> dict[str, Any]:
        """
        解析搜索API响应

        Args:
            response: zai-sdk 返回的搜索响应对象

        Returns:
            标准化的搜索结果字典
        """
        items = []
        summary_parts = []

        # zai-sdk 返回的响应可能包含多种结构
        # 尝试提取搜索结果列表
        search_results = []

        if hasattr(response, 'search_result') and response.search_result:
            search_results = response.search_result
        elif hasattr(response, 'results') and response.results:
            search_results = response.results
        elif hasattr(response, 'data') and response.data:
            search_results = response.data
        elif isinstance(response, dict):
            search_results = response.get('search_result') or response.get('results') or response.get('data', [])
        elif isinstance(response, list):
            search_results = response

        for idx, item in enumerate(search_results):
            if isinstance(item, dict):
                parsed = {
                    "title": item.get("title", f"结果{idx+1}"),
                    "url": item.get("url", item.get("link", "")),
                    "content": item.get("content", item.get("snippet", item.get("abstract", ""))),
                    "source": item.get("source", item.get("media", "")),
                    "date": item.get("date", item.get("publish_time", "")),
                }
            elif hasattr(item, '__dict__'):
                parsed = {
                    "title": getattr(item, 'title', f"结果{idx+1}"),
                    "url": getattr(item, 'url', getattr(item, 'link', '')),
                    "content": getattr(item, 'content', getattr(item, 'snippet', getattr(item, 'abstract', ''))),
                    "source": getattr(item, 'source', getattr(item, 'media', '')),
                    "date": getattr(item, 'date', getattr(item, 'publish_time', '')),
                }
            else:
                parsed = {
                    "title": f"结果{idx+1}",
                    "url": "",
                    "content": str(item),
                    "source": "",
                    "date": "",
                }

            items.append(parsed)
            if parsed["content"]:
                summary_parts.append(f"- {parsed['title']}: {parsed['content'][:200]}")

        return {
            "items": items,
            "summary": "\n".join(summary_parts) if summary_parts else "未找到相关结果",
            "total": len(items)
        }

    def search_stock_news(self, symbol: str, stock_name: str = "") -> dict[str, Any]:
        """
        搜索股票相关新闻（投研专用接口）

        Args:
            symbol: 股票代码，如 "300253.SZ"
            stock_name: 股票名称，如 "卫宁健康"

        Returns:
            包含多个维度搜索结果的综合字典
        """
        code = symbol.split(".")[0] if "." in symbol else symbol
        name = stock_name or code

        # 多维度搜索
        queries = [
            (f"{name} {code} 最新消息 股票", "oneWeek"),
            (f"{name} 财报 业绩 最新", "oneMonth"),
        ]

        all_items = []
        summaries = []

        for query, recency in queries:
            result = self.search(query=query, count=8, recency_filter=recency)
            if result.get("items"):
                all_items.extend(result["items"])
            if result.get("summary"):
                summaries.append(f"**{query}**:\n{result['summary']}")

        # 去重（按URL）
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
            "summary": "\n\n".join(summaries),
            "total": len(unique_items[:15])
        }

    def connect(self) -> None:
        """初始化客户端连接"""
        self._get_client()

    def disconnect(self) -> None:
        """断开连接"""
        self._client = None
        logger.info("WebSearch client disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
