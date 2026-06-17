# Agent Stock — 事件驱动交易机器人

## 项目概述
基于事件驱动策略的自动化股票交易系统，通过Robinhood MCP Server或robin_stocks库执行交易。

## 快速开始

```bash
# 第一步：在Claude Code中注册 Robinhood MCP（官方方式）
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
# 然后在Claude Code输入 /mcp，选择 robinhood-trading 并完成认证

# 安装依赖
pip install -r requirements.txt

# 配置账户（robin_stocks备用模式才需要）
cp .env.example .env

# 模拟运行（不真实下单）
python main.py --dry-run --scan-once

# 列出Robinhood MCP可用工具
python main.py --list-mcp-tools

# 实盘运行
python main.py --live
```

## 架构

```
main.py                  # 入口，CLI参数解析，定时任务
src/
  robinhood_client.py    # Robinhood连接层（MCP优先，robin_stocks备用）
  screener.py            # 股票筛选器（RSI、PE、PB、成交量）
  event_monitor.py       # 事件监控（财报日历、内部人买入）
  risk_manager.py        # 风控（仓位计算、止损止盈、单日限额）
  strategy.py            # 主策略逻辑（整合以上模块）
```

## Robinhood MCP Server
- URL: https://agent.robinhood.com/mcp/trading
- 注册命令: `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`
- 配置位置: .claude/settings.json
- 协议: HTTP transport (MCP 2025-03-26)
- 备用: robin_stocks Python库（自动降级）

## 策略逻辑
1. 技术+基本面筛选（RSI<40, PE<25, 成交量放大1.5x）
2. 事件催化剂匹配（财报前1-14天, 内部人净买入7天内）
3. 仓位计算（单只最大5%，总仓位最大10只）
4. 限价买入 + 自动止损单
5. 止损7% / 止盈20%

## 注意事项
- 默认为模拟模式，需加 --live 才真实下单
- 实盘前请先回测验证策略
- robin_stocks是非官方API，Robinhood随时可能更改接口
