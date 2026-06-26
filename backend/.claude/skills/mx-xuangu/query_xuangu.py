"""临时调用妙想选股API，打印 dataList 结构与结果。"""
import os
import sys
import json
import requests
from pathlib import Path

def main(query: str):
    api_key = os.environ.get("MX_APIKEY")
    if not api_key:
        print("MX_APIKEY 未设置", file=sys.stderr)
        sys.exit(1)
    headers = {"Content-Type": "application/json", "apikey": api_key}
    payload = {"keyword": query}
    r = requests.post("https://mkapi2.dfcfs.com/finskillshub/api/claw/stock-screen",
                      headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "last_response.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    inner = data.get("data", {}).get("data", {})
    # 尝试多个可能的 dataList 路径
    keys = list(inner.keys())
    print("inner keys:", keys)
    result = inner.get("result") or inner.get("allResults", {}).get("result")
    if not result:
        print("no result, dumping inner keys only")
        return
    cols = result.get("columns", [])
    rows = result.get("dataList", [])
    print(f"columns={len(cols)}, rows={len(rows)}")
    if cols:
        print("column keys:", [c.get("key") for c in cols][:20])
    # 打印关键统计
    cond = inner.get("responseConditionList", [])
    total = inner.get("totalCondition", {})
    print("conditions:")
    for c in cond:
        print(f"  - {c.get('describe')}: {c.get('stockCount')}")
    if isinstance(total, dict):
        print("total:", total.get("describe"), "->", total.get("stockCount"))
    else:
        print("totalCondition(raw):", str(total)[:200])
    if rows:
        first = rows[0]
        print("first row keys:", list(first.keys()))
        # 模糊匹配动态字段名
        def pick(row, *prefixes):
            for k, v in row.items():
                for p in prefixes:
                    if p in k:
                        return v
            return None
        print(f"\n共 {len(rows)} 只:")
        for r in rows:
            code = r.get('SECURITY_CODE')
            name = r.get('SECURITY_SHORT_NAME')
            price = r.get('NEWEST_PRICE')
            chg = r.get('CHG')
            turnover = pick(r, '换手率平均值', 'TURNOVER_RATE')
            liangbi = pick(r, 'LIANGBI', '量比')
            interval_chg = pick(r, 'INTERVAL_CHG', '区间涨跌幅')
            cmv = pick(r, 'CIRCULATION_MARKET_VALUE', '流通市值')
            print(f"  {code} {name:<8} 价={price:<7} 今日={chg:>6}% 5日均换手={turnover}% "
                  f"量比={liangbi} 20日涨幅={interval_chg}% 流通市值={cmv}万")

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "非ST股 非停牌 非退市 流通市值小于200亿 股价小于30元 换手率3%至8% 量比1至2.5 近20日涨幅小于10% A股"
    main(q)
