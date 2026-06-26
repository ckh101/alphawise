"""
自选股路由

CRUD + 详情复合接口
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from harness.core.database import WatchlistItem as WatchlistItemModel
from harness.core.database import get_session
from harness.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/watchlist", tags=["自选股"])


# ========== Request Models ==========

class ItemCreate(BaseModel):
    symbol: str
    name: str = ""
    group_name: str = "默认"
    notes: str = ""


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    group_name: Optional[str] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = None


# ========== Helpers ==========

def _item_to_dict(item: WatchlistItemModel) -> dict:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "name": item.name,
        "group_name": item.group_name,
        "sort_order": item.sort_order,
        "notes": item.notes,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _normalize_symbol(symbol: str) -> str:
    """纯数字自动补全市场后缀"""
    symbol = symbol.strip().upper()
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith(("6",)):
            return symbol + ".SH"
        elif symbol.startswith(("0", "3")):
            return symbol + ".SZ"
        elif symbol.startswith(("8", "4")):
            return symbol + ".BJ"
    return symbol


# ========== Routes ==========

@router.get("/items")
async def list_items():
    """列出所有自选股，按 group_name + sort_order 排序"""
    session = get_session()
    try:
        items = (
            session.query(WatchlistItemModel)
            .order_by(WatchlistItemModel.group_name, WatchlistItemModel.sort_order, WatchlistItemModel.id.desc())
            .all()
        )
        return {"code": 0, "data": {"items": [_item_to_dict(i) for i in items]}}
    finally:
        session.close()


@router.post("/items")
async def add_item(body: ItemCreate):
    """添加自选股（按 symbol 去重）"""
    symbol = _normalize_symbol(body.symbol)
    session = get_session()
    try:
        existing = session.query(WatchlistItemModel).filter_by(symbol=symbol).first()
        if existing:
            return {"code": 0, "data": _item_to_dict(existing), "message": "已在自选股中"}

        # 尝试从 TDX 获取股票名称
        name = body.name
        if not name:
            try:
                import importlib.util
                from pathlib import Path
                info_path = Path(__file__).parent.parent.parent.parent / "skills" / "builtin" / "tdx-stock-info"
                spec = importlib.util.spec_from_file_location("tdx_info_wl", str(info_path / "skill.py"))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                info = mod.get_stock_info(symbol)
                name = info.get("name", "")
            except Exception:
                pass

        item = WatchlistItemModel(
            symbol=symbol,
            name=name,
            group_name=body.group_name,
            notes=body.notes,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        logger.info(f"[watchlist] Added: {symbol} ({name})")
        return {"code": 0, "data": _item_to_dict(item)}
    except Exception as e:
        session.rollback()
        logger.error(f"[watchlist] Failed to add: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/items/{item_id}")
async def delete_item(item_id: int):
    """删除自选股"""
    session = get_session()
    try:
        item = session.query(WatchlistItemModel).filter_by(id=item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="不存在")
        session.delete(item)
        session.commit()
        return {"code": 0, "message": "已删除"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"[watchlist] Failed to delete: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.put("/items/{item_id}")
async def update_item(item_id: int, body: ItemUpdate):
    """更新自选股备注/分组"""
    session = get_session()
    try:
        item = session.query(WatchlistItemModel).filter_by(id=item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="不存在")
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(item, k, v)
        session.commit()
        session.refresh(item)
        return {"code": 0, "data": _item_to_dict(item)}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"[watchlist] Failed to update: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/items/{item_id}/detail")
async def get_item_detail(item_id: int):
    """复合接口：quote + stock_info + kline(daily/weekly/monthly)"""
    session = get_session()
    try:
        item = session.query(WatchlistItemModel).filter_by(id=item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="不存在")
    finally:
        session.close()

    symbol = item.symbol
    result = {"symbol": symbol, "name": item.name, "quote": None, "info": None, "klines": {}}

    # 动态加载 TDX 技能
    import importlib.util
    from pathlib import Path
    skills_path = Path(__file__).parent.parent.parent.parent / "skills" / "builtin"

    # 实时行情
    try:
        spec = importlib.util.spec_from_file_location("wl_quote", str(skills_path / "tdx-realtime-quote" / "skill.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        quotes = mod.get_realtime_quote([symbol])
        if quotes:
            result["quote"] = quotes[0]
    except Exception as e:
        logger.warning(f"[watchlist] quote failed: {e}")

    # 基本信息
    try:
        spec = importlib.util.spec_from_file_location("wl_info", str(skills_path / "tdx-stock-info" / "skill.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result["info"] = mod.get_stock_info(symbol)
    except Exception as e:
        logger.warning(f"[watchlist] stock_info failed: {e}")

    # K线（日/周/月，取近 6 个月）
    try:
        spec = importlib.util.spec_from_file_location("wl_kline", str(skills_path / "tdx-kline" / "skill.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from datetime import date, timedelta
        start = (date.today() - timedelta(days=180)).strftime("%Y-%m-%d")
        for period in ["daily", "weekly", "monthly"]:
            try:
                kdata = mod.get_kline(symbol, period, start_date=start)
                result["klines"][period] = kdata
            except Exception:
                result["klines"][period] = []
    except Exception as e:
        logger.warning(f"[watchlist] kline failed: {e}")

    return {"code": 0, "data": result}
