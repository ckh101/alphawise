"""
交易日历服务

使用 baostock 获取沪深交易所交易日历，本地缓存。
"""

import json
from datetime import date, timedelta

from harness.core.database import get_session
from harness.core.database import Setting
from harness.core.logger import get_logger

logger = get_logger(__name__)

CACHE_KEY = "trade_calendar.cache"


def _fetch_year_from_baostock(year: int) -> list[str]:
    """从 baostock 获取指定年份的交易日列表"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        rs = bs.query_trade_dates(start_date=start, end_date=end)
        days = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            # row: [calendar_date, is_trading_day]  例如 ["2026-06-09", "1"]
            if len(row) >= 2 and str(row[1]).strip() == "1":
                days.append(row[0])
        return days
    finally:
        bs.logout()


def _load_cache() -> dict:
    """从数据库加载缓存"""
    session = get_session()
    try:
        row = session.query(Setting).filter_by(key=CACHE_KEY).first()
        if row:
            return json.loads(row.value)
    except Exception as e:
        logger.warning(f"[trade_calendar] Failed to load cache: {e}")
    finally:
        session.close()
    return {}


def _save_cache(data: dict) -> None:
    """保存缓存到数据库"""
    session = get_session()
    try:
        row = session.query(Setting).filter_by(key=CACHE_KEY).first()
        if row:
            row.value = json.dumps(data, ensure_ascii=False)
        else:
            session.add(Setting(key=CACHE_KEY, value=json.dumps(data, ensure_ascii=False)))
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"[trade_calendar] Failed to save cache: {e}")
    finally:
        session.close()


def _ensure_cache_covers_current_year() -> None:
    """确保缓存覆盖当前年份 ± 1 年"""
    today = date.today()
    needed_years = {today.year - 1, today.year, today.year + 1}
    cache = _load_cache()
    missing = [y for y in needed_years if str(y) not in cache]
    if not missing:
        return
    for year in missing:
        try:
            days = _fetch_year_from_baostock(year)
            cache[str(year)] = days
            logger.info(f"[trade_calendar] Fetched {year}: {len(days)} trading days")
        except Exception as e:
            logger.warning(f"[trade_calendar] Failed to fetch {year}: {e}")
    _save_cache(cache)


def _get_trading_days_set() -> set[str]:
    """获取所有缓存中的交易日集合"""
    _ensure_cache_covers_current_year()
    cache = _load_cache()
    result = set()
    for days in cache.values():
        result.update(days)
    return result


def is_trading_day(date_str: str) -> bool:
    """检查是否为交易日。

    无法获取交易日历时，保守地视为「是交易日」（不跳过任务），
    避免日历源不可用导致定时任务被误跳过。
    """
    _ensure_cache_covers_current_year()
    cache = _load_cache()
    year = date_str[:4]
    year_days = cache.get(year)
    if year_days is None:
        # 尝试拉取
        try:
            days = _fetch_year_from_baostock(int(year))
            cache[year] = days
            _save_cache(cache)
            year_days = days
        except Exception:
            logger.warning(
                f"[trade_calendar] Cannot determine {date_str}, "
                "calendar unavailable -> assume trading day (do not skip)"
            )
            return True
    return date_str in year_days


def get_trading_days(year: int) -> list[str]:
    """获取某年所有交易日"""
    _ensure_cache_covers_current_year()
    cache = _load_cache()
    year_str = str(year)
    if year_str in cache:
        return cache[year_str]
    try:
        days = _fetch_year_from_baostock(year)
        cache[year_str] = days
        _save_cache(cache)
        return days
    except Exception as e:
        logger.warning(f"[trade_calendar] Failed to get trading days for {year}: {e}")
        return []


def get_next_trading_day(date_str: str) -> str:
    """获取下一个交易日"""
    all_days = _get_trading_days_set()
    d = date.fromisoformat(date_str)
    for i in range(1, 30):
        candidate = (d + timedelta(days=i)).isoformat()
        if candidate in all_days:
            return candidate
    return date_str
