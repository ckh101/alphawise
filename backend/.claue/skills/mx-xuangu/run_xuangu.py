#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import io

# 强制UTF-8输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 导入并执行选股脚本
import mx_xuangu

if __name__ == "__main__":
    query = "主力吸筹末期准备启动拉升，非ST股票，正常交易个股"
    print(f"正在执行选股查询: {query}")
    mx_xuangu.run_main([query])
