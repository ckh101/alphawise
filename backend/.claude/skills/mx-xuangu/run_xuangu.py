# -*- coding: utf-8 -*-
"""临时选股运行脚本，解决Windows GBK编码问题"""
import subprocess
import sys
import os

# 设置环境变量确保UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'

query = "换手率在3%到8%之间 涨幅在-1%到3%之间 股价小于30元 量比大于1.5 量比小于5 市盈率大于0 非ST股 非停牌股 A股"

script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mx_xuangu.py')
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output_raw.txt')

r = subprocess.run(
    [sys.executable, script_path, query],
    capture_output=True,
    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

# 尝试UTF-8解码，失败则用GBK
try:
    stdout = r.stdout.decode('utf-8')
except:
    stdout = r.stdout.decode('gbk', errors='replace')

try:
    stderr = r.stderr.decode('utf-8')
except:
    stderr = r.stderr.decode('gbk', errors='replace')

with open(output_path, 'w', encoding='utf-8') as f:
    f.write("=== STDOUT ===\n")
    f.write(stdout)
    f.write("\n=== STDERR ===\n")
    f.write(stderr)

print(f"Done. Output saved to {output_path}")
