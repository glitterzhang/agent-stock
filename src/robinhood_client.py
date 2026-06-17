"""
Robinhood连接层 — 支持 robin_stocks 直连 + MCP Server 两种模式
"""
import os
import json
import requests
import pyotp
import robin_stocks.robinhood as r
from dotenv import load_dotenv

load_dotenv()


class RobinhoodMCPClient:
    """通过 Robinhood MCP Server 执行交易操作"""

    def __init__(self, mcp_url: str = None):
        self.mcp_url = mcp_url or os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用MCP工具（JSON-RPC 2.0协议）"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        resp = self.session.post(self.mcp_url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if "error" in result:
            raise RuntimeError(f"MCP error: {result['error']}")
        return result.get("result", {})

    def list_tools(self) -> list:
        """列出MCP服务器所有可用工具"""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = self.session.post(self.mcp_url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("tools", [])

    def get_quote(self, symbol: str) -> dict:
        return self._call_tool("get_quote", {"symbol": symbol})

    def get_account(self) -> dict:
        return self._call_tool("get_account", {})

    def place_buy_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        args = {"symbol": symbol, "quantity": quantity, "order_type": order_type}
        if limit_price:
            args["limit_price"] = limit_price
        return self._call_tool("place_order", {"side": "buy", **args})

    def place_sell_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        args = {"symbol": symbol, "quantity": quantity, "order_type": order_type}
        if limit_price:
            args["limit_price"] = limit_price
        return self._call_tool("place_order", {"side": "sell", **args})

    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> dict:
        return self._call_tool("place_order", {
            "symbol": symbol, "quantity": quantity,
            "order_type": "stop_loss", "stop_price": stop_price, "side": "sell"
        })

    def get_positions(self) -> list:
        return self._call_tool("get_positions", {})

    def get_open_orders(self) -> list:
        return self._call_tool("get_open_orders", {})

    def cancel_order(self, order_id: str) -> dict:
        return self._call_tool("cancel_order", {"order_id": order_id})


class RobinhoodDirectClient:
    """通过 robin_stocks 库直接连接Robinhood（MCP不可用时的备用）"""

    def __init__(self):
        self._logged_in = False

    def login(self):
        email = os.getenv("ROBINHOOD_EMAIL")
        password = os.getenv("ROBINHOOD_PASSWORD")
        mfa_key = os.getenv("ROBINHOOD_MFA_KEY")

        if not email or not password:
            raise ValueError("请在.env文件中设置 ROBINHOOD_EMAIL 和 ROBINHOOD_PASSWORD")

        mfa_code = pyotp.TOTP(mfa_key).now() if mfa_key else None
        r.login(email, password, mfa_code=mfa_code)
        self._logged_in = True
        print("✅ Robinhood登录成功")

    def _ensure_logged_in(self):
        if not self._logged_in:
            self.login()

    def get_quote(self, symbol: str) -> dict:
        self._ensure_logged_in()
        price = r.get_latest_price(symbol)
        fundamentals = r.get_fundamentals(symbol)
        return {
            "symbol": symbol,
            "price": float(price[0]) if price else None,
            "fundamentals": fundamentals[0] if fundamentals else {}
        }

    def get_account(self) -> dict:
        self._ensure_logged_in()
        profile = r.load_account_profile()
        portfolio = r.load_portfolio_profile()
        return {"profile": profile, "portfolio": portfolio}

    def place_buy_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        self._ensure_logged_in()
        if order_type == "market":
            return r.order_buy_market(symbol, quantity)
        elif order_type == "limit" and limit_price:
            return r.order_buy_limit(symbol, quantity, limit_price)
        raise ValueError(f"不支持的订单类型: {order_type}")

    def place_sell_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        self._ensure_logged_in()
        if order_type == "market":
            return r.order_sell_market(symbol, quantity)
        elif order_type == "limit" and limit_price:
            return r.order_sell_limit(symbol, quantity, limit_price)
        raise ValueError(f"不支持的订单类型: {order_type}")

    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> dict:
        self._ensure_logged_in()
        return r.order_sell_stop_loss(symbol, quantity, stop_price)

    def get_positions(self) -> list:
        self._ensure_logged_in()
        return r.get_open_stock_positions()

    def get_open_orders(self) -> list:
        self._ensure_logged_in()
        return r.get_all_open_stock_orders()

    def cancel_order(self, order_id: str) -> dict:
        self._ensure_logged_in()
        return r.cancel_stock_order(order_id)


def get_client(prefer_mcp: bool = True):
    """
    返回合适的客户端。
    优先使用MCP Server；如果MCP不可达，自动降级到robin_stocks直连。
    """
    if prefer_mcp:
        mcp_url = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
        client = RobinhoodMCPClient(mcp_url)
        try:
            tools = client.list_tools()
            print(f"✅ MCP Server连接成功，可用工具: {[t['name'] for t in tools]}")
            return client
        except Exception as e:
            print(f"⚠️  MCP Server不可达 ({e})，降级到robin_stocks直连模式")

    client = RobinhoodDirectClient()
    client.login()
    return client
