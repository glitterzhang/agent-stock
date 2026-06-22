"""
Robinhood connection layer — supports robin_stocks direct connection + MCP Server modes
"""
import os
import base64
import json
import requests
import pyotp
import robin_stocks.robinhood as r
from dotenv import load_dotenv

load_dotenv()


class RobinhoodMCPClient:
    """Execute trading operations via the Robinhood MCP Server"""

    def __init__(self, mcp_url: str = None):
        self.mcp_url = mcp_url or os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Invoke an MCP tool (JSON-RPC 2.0 protocol)"""
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
        """List all tools available on the MCP server"""
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
    """Connect directly to Robinhood via the robin_stocks library (fallback when MCP is unavailable)"""

    def __init__(self):
        self._logged_in = False

    def login(self):
        email = os.getenv("ROBINHOOD_EMAIL")
        password = os.getenv("ROBINHOOD_PASSWORD")
        mfa_key = os.getenv("ROBINHOOD_MFA_KEY")
        pickle_b64 = os.getenv("ROBINHOOD_PICKLE_B64")

        if not email or not password:
            raise ValueError("Please set ROBINHOOD_EMAIL and ROBINHOOD_PASSWORD in the .env file")

        # Restore cached session token to avoid triggering new-device push notification
        if pickle_b64:
            import pickle
            token_dir = os.path.expanduser("~/.tokens")
            os.makedirs(token_dir, exist_ok=True)
            pickle_path = os.path.join(token_dir, "robinhood.pickle")
            with open(pickle_path, "wb") as f:
                f.write(base64.b64decode(pickle_b64))
            print("✅ Restored cached Robinhood session token")

        mfa_code = pyotp.TOTP(mfa_key).now() if mfa_key else None
        r.login(email, password, mfa_code=mfa_code, store_session=True)
        self._logged_in = True
        print("✅ Robinhood login successful")

        # Print current pickle as base64 so you can save it to GitHub Secrets
        pickle_path = os.path.expanduser("~/.tokens/robinhood.pickle")
        if os.path.exists(pickle_path) and not pickle_b64:
            with open(pickle_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
            print(f"\n📋 Save this to GitHub Secret ROBINHOOD_PICKLE_B64 to skip future device approvals:\n{encoded}\n")

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
            return r.order_buy_market(symbol, quantity, timeInForce="gfd")
        elif order_type == "limit" and limit_price:
            return r.order_buy_limit(symbol, quantity, limit_price, timeInForce="gfd")
        raise ValueError(f"Unsupported order type: {order_type}")

    def place_sell_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        self._ensure_logged_in()
        if order_type == "market":
            return r.order_sell_market(symbol, quantity, timeInForce="gfd")
        elif order_type == "limit" and limit_price:
            return r.order_sell_limit(symbol, quantity, limit_price, timeInForce="gfd")
        raise ValueError(f"Unsupported order type: {order_type}")

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
    Return the appropriate client.
    Prefer MCP Server; automatically fall back to robin_stocks direct connection if MCP is unreachable.
    """
    if prefer_mcp:
        mcp_url = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
        client = RobinhoodMCPClient(mcp_url)
        try:
            tools = client.list_tools()
            print(f"✅ MCP Server connected, available tools: {[t['name'] for t in tools]}")
            return client
        except Exception as e:
            print(f"⚠️  MCP Server unreachable ({e}), falling back to robin_stocks direct mode")

    client = RobinhoodDirectClient()
    client.login()
    return client
