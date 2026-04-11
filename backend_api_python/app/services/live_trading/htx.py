"""
HTX (Huobi) direct REST client for spot and USDT-margined perpetual swap.

References:
- Spot base URL: https://api.htx.com  (legacy: api.huobi.pro)
- USDT swap base URL: https://api.hbdm.com
- Spot auth: query params with HmacSHA256 signature
- Swap auth: query params with HmacSHA256 signature, request body in JSON
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse
import datetime
import time

import logging

from app.services.live_trading.base import BaseRestClient, LiveOrderResult, LiveTradingError
from app.services.live_trading.symbols import to_htx_contract_code, to_htx_spot_symbol

logger = logging.getLogger(__name__)


class HtxClient(BaseRestClient):
    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = "https://api.htx.com",
        futures_base_url: str = "https://api.hbdm.com",
        timeout_sec: float = 15.0,
        market_type: str = "swap",
        broker_id: str = "",
    ):
        chosen_base = futures_base_url if str(market_type or "").strip().lower() == "swap" else base_url
        super().__init__(base_url=chosen_base, timeout_sec=timeout_sec)
        self.spot_base_url = (base_url or "https://api.htx.com").rstrip("/")
        self.futures_base_url = (futures_base_url or "https://api.hbdm.com").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.market_type = (market_type or "swap").strip().lower()
        self.broker_id = (broker_id or "").strip()
        if self.market_type not in ("spot", "swap"):
            self.market_type = "swap"
        if not self.api_key or not self.secret_key:
            raise LiveTradingError("Missing HTX api_key/secret_key")

        self._spot_account_id: Optional[str] = None
        self._contract_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._contract_cache_ttl_sec = 300.0
        self._lever_cache: Dict[str, int] = {}
        self._account_type: Optional[int] = None  # 1=non-unified, 2=unified
        self._account_type_ts: float = 0.0

    @staticmethod
    def _format_swap_client_order_id(client_order_id: Optional[str]) -> Optional[int]:
        """HTX swap/linear-swap client_order_id must be a pure numeric long (1~9223372036854775807)."""
        if not client_order_id:
            return None
        digits = "".join(c for c in str(client_order_id) if c.isdigit())
        if not digits:
            digits = str(int(time.time() * 1000))
        val = int(digits[-18:])
        return val if 0 < val <= 9223372036854775807 else None

    def _format_spot_client_order_id(self, client_order_id: Optional[str]) -> str:
        prefix = str(self.broker_id or "").strip()
        raw = str(client_order_id or "").strip()
        if not prefix and not raw:
            return ""

        if not raw:
            raw = str(int(time.time() * 1000))

        allowed = []
        for ch in raw:
            if ch.isalnum() or ch in ("_", "-"):
                allowed.append(ch)
        suffix = "".join(allowed).strip("-_")
        if not suffix:
            suffix = str(int(time.time() * 1000))

        if prefix:
            if suffix.startswith(prefix):
                combined = suffix
            else:
                combined = f"{prefix}-{suffix}"
        else:
            combined = suffix
        return combined[:64]

    @staticmethod
    def _utc_ts() -> str:
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _to_dec(x: Any) -> Decimal:
        try:
            return Decimal(str(x))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _floor_to_int(value: Decimal) -> int:
        try:
            return int(value.to_integral_value(rounding=ROUND_DOWN))
        except Exception:
            return 0

    def _sign_params(self, *, method: str, base_url: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        signed = dict(params or {})
        signed["AccessKeyId"] = self.api_key
        signed["SignatureMethod"] = "HmacSHA256"
        signed["SignatureVersion"] = "2"
        signed["Timestamp"] = self._utc_ts()
        encoded = urlencode(sorted((str(k), str(v)) for k, v in signed.items()))
        host = urlparse(base_url).netloc
        payload = "\n".join([str(method or "GET").upper(), host, path, encoded])
        digest = hmac.new(self.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        signed["Signature"] = base64.b64encode(digest).decode("utf-8")
        return signed

    def _spot_public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        old_base = self.base_url
        self.base_url = self.spot_base_url
        try:
            code, data, text = self._request(method, path, params=params)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX spot HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX spot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _spot_private_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        signed_params = self._sign_params(method=method, base_url=self.spot_base_url, path=path, params=params or {})
        old_base = self.base_url
        self.base_url = self.spot_base_url
        try:
            code, data, text = self._request(method, path, params=signed_params, json_body=json_body)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX spot HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX spot error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _swap_private_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        signed_params = self._sign_params(method=method, base_url=self.futures_base_url, path=path, params=params or {})
        old_base = self.base_url
        self.base_url = self.futures_base_url
        try:
            code, data, text = self._request(method, path, params=signed_params, json_body=json_body)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX swap HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX swap error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def _swap_public_request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        old_base = self.base_url
        self.base_url = self.futures_base_url
        try:
            code, data, text = self._request(method, path, params=params)
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX swap HTTP {code}: {text[:500]}")
        if isinstance(data, dict) and str(data.get("status") or "").lower() == "error":
            raise LiveTradingError(f"HTX swap error: {data}")
        return data if isinstance(data, dict) else {"raw": data}

    def ping(self) -> bool:
        try:
            if self.market_type == "spot":
                self._spot_public_request("GET", "/v1/common/timestamp")
            else:
                self._swap_public_request("GET", "/linear-swap-api/v1/swap_contract_info")
            return True
        except Exception:
            return False

    def _detect_account_type(self) -> int:
        """Detect HTX futures account type via v3 endpoint.
        Returns 1=non-unified (cross/isolated), 2=unified, 0=unknown.
        If unified (2), automatically attempts to switch to non-unified (1).
        Result is cached for 10 minutes."""
        now = time.time()
        if self._account_type is not None and (now - self._account_type_ts) < 600:
            return self._account_type
        try:
            raw = self._swap_private_request("GET", "/linear-swap-api/v3/swap_unified_account_type")
            v3_code = raw.get("code")
            if v3_code is not None and int(v3_code) != 200:
                logger.warning("HTX swap_unified_account_type returned code=%s msg=%s", v3_code, raw.get("msg"))
                self._account_type = 0
                self._account_type_ts = now
                return 0
            data = raw.get("data") or {}
            if isinstance(data, dict):
                at = int(data.get("account_type") or 0)
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                at = int(data[0].get("account_type") or 0)
            else:
                at = 0
            self._account_type = at if at in (1, 2) else 0
            self._account_type_ts = now
            logger.info("HTX account_type=%s (%s)", self._account_type, "non-unified" if at == 1 else "unified" if at == 2 else "unknown")

            if self._account_type == 2:
                switched = self._try_switch_to_non_unified()
                if switched:
                    self._account_type = 1
                    self._account_type_ts = now

            return self._account_type
        except Exception as e:
            logger.warning("HTX detect account type failed: %s", e)
            self._account_type = 0
            self._account_type_ts = now
            return 0

    def _try_switch_to_non_unified(self) -> bool:
        """Attempt to switch HTX asset mode to single-currency margin (asset_mode=0)
        via the official V5 endpoint to resolve error 6002.
        See: https://www.htx.com/zh-cn/opend/newApiPages/?id=8cb89359-77b5-11ed-9966-1957dd1a995
        Falls back to v3 swap_switch_account_type if v5 fails."""
        # Method 1: V5 /v5/account/asset_mode (official recommended)
        try:
            logger.info("HTX switching asset_mode to 0 (single-currency margin) via V5 API...")
            raw = self._swap_private_request_lenient(
                "POST", "/v5/account/asset_mode",
                json_body={"asset_mode": 0},
            )
            # V5 success: {"status":"ok","data":{"asset_mode":0}} or {"code":200,...}
            status = str(raw.get("status") or "").lower()
            v5_code = raw.get("code")
            if status == "ok" or (v5_code is not None and int(v5_code) == 200):
                new_mode = (raw.get("data") or {}).get("asset_mode")
                logger.info("HTX asset_mode switched to %s via V5 successfully", new_mode)
                return True
            err_msg = raw.get("err_msg") or raw.get("msg") or ""
            err_code = raw.get("err_code") or raw.get("code") or ""
            logger.warning("HTX V5 asset_mode switch returned: code=%s msg=%s", err_code, err_msg)
        except Exception as e:
            logger.warning("HTX V5 asset_mode switch exception: %s", e)

        # Method 2: V3 swap_switch_account_type (fallback)
        try:
            logger.info("HTX falling back to V3 swap_switch_account_type...")
            raw = self._swap_private_request_lenient(
                "POST", "/linear-swap-api/v3/swap_switch_account_type",
                json_body={"account_type": 1},
            )
            v3_code = raw.get("code")
            if v3_code is not None and int(v3_code) == 200:
                new_type = (raw.get("data") or {}).get("account_type")
                logger.info("HTX account switched to non-unified via V3 (type=%s)", new_type)
                return True
            err_msg = raw.get("msg") or raw.get("err_msg") or ""
            logger.warning("HTX V3 switch account type failed: code=%s msg=%s", v3_code, err_msg)
        except Exception as e:
            logger.warning("HTX V3 switch account type exception: %s", e)

        return False

    def _get_spot_account_id(self) -> str:
        if self._spot_account_id:
            return self._spot_account_id
        raw = self._spot_private_request("GET", "/v1/account/accounts")
        data = raw.get("data") or []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").lower() == "spot" and str(item.get("state") or "").lower() in ("working", ""):
                    self._spot_account_id = str(item.get("id") or "")
                    if self._spot_account_id:
                        return self._spot_account_id
            for item in data:
                if isinstance(item, dict) and item.get("id"):
                    self._spot_account_id = str(item.get("id"))
                    return self._spot_account_id
        raise LiveTradingError("HTX spot account id not found")

    def get_accounts(self) -> Any:
        if self.market_type == "spot":
            return self._spot_private_request("GET", "/v1/account/accounts")
        return self.get_balance()

    _BALANCE_ENDPOINTS_ISOLATED_FIRST = [
        ("v1-isolated", "POST", "/linear-swap-api/v1/swap_account_info", {}),
        ("v1-cross", "POST", "/linear-swap-api/v1/swap_cross_account_info", {"margin_account": "USDT"}),
        ("v3-unified", "GET", "/linear-swap-api/v3/unified_account_info", None),
    ]

    _BALANCE_ENDPOINTS_UNIFIED_FIRST = [
        ("v3-unified", "GET", "/linear-swap-api/v3/unified_account_info", None),
        ("v1-cross", "POST", "/linear-swap-api/v1/swap_cross_account_info", {"margin_account": "USDT"}),
        ("v1-isolated", "POST", "/linear-swap-api/v1/swap_account_info", {}),
    ]

    def _swap_private_request_lenient(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Like _swap_private_request but does NOT raise on status='error'.
        Returns the raw dict so the caller can inspect both status-based and code-based errors."""
        signed_params = self._sign_params(method=method, base_url=self.futures_base_url, path=path, params=kwargs.get("params") or {})
        old_base = self.base_url
        self.base_url = self.futures_base_url
        try:
            code, data, text = self._request(method, path, params=signed_params, json_body=kwargs.get("json_body"))
        finally:
            self.base_url = old_base
        if code >= 400:
            raise LiveTradingError(f"HTX swap HTTP {code}: {text[:500]}")
        return data if isinstance(data, dict) else {"raw": data}

    def get_balance(self) -> Any:
        if self.market_type == "spot":
            account_id = self._get_spot_account_id()
            return self._spot_private_request("GET", f"/v1/account/accounts/{account_id}/balance")
        acct = self._detect_account_type()
        endpoints = self._BALANCE_ENDPOINTS_UNIFIED_FIRST if acct == 2 else self._BALANCE_ENDPOINTS_ISOLATED_FIRST
        last_err: Optional[Exception] = None
        for tag, method, path, body in endpoints:
            try:
                if tag == "v3-unified":
                    raw = self._swap_private_request_lenient(method, path)
                    # v3 may return {"code": 200, "data": [...]} or {"status": "error", ...}
                    if str(raw.get("status") or "").lower() == "error":
                        logger.warning("HTX %s returned error: err_code=%s msg=%s", tag, raw.get("err_code"), raw.get("err_msg"))
                        last_err = LiveTradingError(f"HTX v3 unified error: {raw}")
                        continue
                    v3_code = raw.get("code")
                    if v3_code is not None and int(v3_code) != 200:
                        logger.warning("HTX %s returned code=%s msg=%s", tag, v3_code, raw.get("msg"))
                        continue
                else:
                    if body is not None:
                        raw = self._swap_private_request(method, path, json_body=body)
                    else:
                        raw = self._swap_private_request(method, path)
                data = raw.get("data")
                if data is not None:
                    logger.info("HTX balance succeeded via %s", tag)
                    return raw
            except (LiveTradingError, Exception) as e:
                logger.warning("HTX %s balance failed: %s", tag, e)
                last_err = e
        # Last resort: try reading USDT from the spot wallet.
        # During HTX's multi-asset collateral migration, all swap endpoints may return 6002
        # but the spot account is usually accessible.
        try:
            logger.info("HTX swap balance unavailable, falling back to spot USDT balance")
            spot_acct_id = self._get_spot_account_id()
            spot_raw = self._spot_private_request("GET", f"/v1/account/accounts/{spot_acct_id}/balance")
            spot_list = ((spot_raw.get("data") or {}).get("list") or []) if isinstance(spot_raw, dict) else []
            usdt_total = 0.0
            usdt_avail = 0.0
            for item in spot_list:
                if not isinstance(item, dict):
                    continue
                if str(item.get("currency") or "").upper() != "USDT":
                    continue
                bal = float(item.get("balance") or 0)
                typ = str(item.get("type") or "").lower()
                if typ == "trade":
                    usdt_avail += bal
                usdt_total += bal
            if usdt_total > 0 or usdt_avail > 0:
                logger.info("HTX spot USDT fallback: avail=%.4f total=%.4f", usdt_avail, usdt_total)
                return {"data": [{
                    "margin_balance": usdt_total,
                    "margin_static": usdt_total,
                    "margin_available": usdt_avail,
                    "withdraw_available": usdt_avail,
                    "margin_asset": "USDT",
                    "margin_mode": "spot_fallback",
                }]}
        except Exception as spot_e:
            logger.warning("HTX spot USDT fallback also failed: %s", spot_e)

        logger.warning("HTX all balance endpoints failed (acct_type=%s), last error: %s", acct, last_err)
        return {"data": []}

    def get_positions(self, *, symbol: str = "") -> Any:
        if self.market_type == "spot":
            balance = self.get_balance()
            items = (((balance.get("data") or {}).get("list")) if isinstance(balance, dict) else None) or []
            base_asset = ""
            if symbol:
                base_asset = str(symbol).split("/", 1)[0].split(":", 1)[0].strip().upper()
            rows = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                ccy = str(item.get("currency") or "").upper()
                if not ccy or (base_asset and ccy != base_asset):
                    continue
                bal = self._to_dec(item.get("balance") or "0")
                if bal <= 0:
                    continue
                rows.append({
                    "symbol": f"{ccy}/USDT",
                    "bal": float(bal),
                    "availBal": float(self._to_dec(item.get("balance") or "0")),
                    "cost_open": 0,
                    "profit_unreal": 0,
                })
            return {"data": rows}

        body = {"contract_code": to_htx_contract_code(symbol)} if symbol else {}
        acct = self._detect_account_type()
        if acct == 2:
            logger.info("HTX unified account detected — v1 position endpoints may not work; returning empty")
            # Unified account: v1 endpoints typically return 6002; no v3 position query exists.
            # Best-effort: still try isolated, but gracefully return empty on failure.
            try:
                raw = self._swap_private_request("POST", "/linear-swap-api/v1/swap_position_info", json_body=body)
                data = raw.get("data")
                if data is not None:
                    logger.info("HTX positions succeeded via v1-isolated (items=%d)", len(data) if isinstance(data, list) else -1)
                    return raw
            except Exception as e:
                logger.info("HTX unified account v1-isolated position query failed (expected): %s", e)
            return {"data": []}

        position_endpoints = [
            ("v1-isolated", "POST", "/linear-swap-api/v1/swap_position_info", body),
            ("v1-cross", "POST", "/linear-swap-api/v1/swap_cross_position_info", body),
        ]
        last_err: Optional[Exception] = None
        for tag, method, path, req_body in position_endpoints:
            try:
                raw = self._swap_private_request(method, path, json_body=req_body)
                data = raw.get("data")
                if data is not None:
                    logger.info("HTX positions succeeded via %s (items=%d)", tag, len(data) if isinstance(data, list) else -1)
                    return raw
            except (LiveTradingError, Exception) as e:
                logger.warning("HTX %s position failed: %s", tag, e)
                last_err = e
        logger.warning("HTX all position endpoints failed for symbol=%s, last error: %s", symbol, last_err)
        return {"data": []}

    def get_ticker(self, *, symbol: str) -> Dict[str, Any]:
        if self.market_type == "spot":
            raw = self._spot_public_request("GET", "/market/detail/merged", params={"symbol": to_htx_spot_symbol(symbol)})
        else:
            raw = self._swap_public_request("GET", "/linear-swap-ex/market/detail/merged", params={"contract_code": to_htx_contract_code(symbol)})
        tick = raw.get("tick") if isinstance(raw, dict) else {}
        return tick if isinstance(tick, dict) else {}

    def get_contract_info(self, *, symbol: str) -> Dict[str, Any]:
        key = to_htx_contract_code(symbol)
        cached = self._contract_cache.get(key)
        now = time.time()
        if cached:
            ts, obj = cached
            if obj and (now - float(ts or 0)) <= float(self._contract_cache_ttl_sec or 300):
                return obj
        raw = self._swap_public_request("GET", "/linear-swap-api/v1/swap_contract_info", params={"contract_code": key})
        data = raw.get("data") or []
        obj = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        if obj:
            self._contract_cache[key] = (now, obj)
        return obj

    def _base_to_contracts(self, *, symbol: str, qty: float) -> int:
        req = self._to_dec(qty)
        if req <= 0:
            return 0
        info = self.get_contract_info(symbol=symbol) or {}
        contract_size = self._to_dec(info.get("contract_size") or info.get("contractSize") or "1")
        if contract_size <= 0:
            contract_size = Decimal("1")
        contracts = req / contract_size
        val = self._floor_to_int(contracts)
        return val if val > 0 else 1

    def set_leverage(self, *, symbol: str, leverage: float) -> bool:
        if self.market_type == "spot":
            return False
        contract_code = to_htx_contract_code(symbol)
        try:
            lv = int(float(leverage or 1))
        except Exception:
            lv = 1
        if lv < 1:
            lv = 1
        # Try isolated first (avoids 6002 on accounts migrated away from multi-asset collateral)
        try:
            self._swap_private_request(
                "POST",
                "/linear-swap-api/v1/swap_switch_lever_rate",
                json_body={"contract_code": contract_code, "lever_rate": lv},
            )
            self._lever_cache[contract_code] = lv
            return True
        except Exception:
            try:
                self._swap_private_request(
                    "POST",
                    "/linear-swap-api/v1/swap_cross_switch_lever_rate",
                    json_body={"contract_code": contract_code, "lever_rate": lv, "margin_account": "USDT"},
                )
                self._lever_cache[contract_code] = lv
                return True
            except Exception:
                return False

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        reduce_only: bool = False,
        pos_side: str = "",
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        if self.market_type == "spot":
            account_id = self._get_spot_account_id()
            sd = str(side or "").strip().lower()
            if sd not in ("buy", "sell"):
                raise LiveTradingError(f"Invalid side: {side}")
            amount = float(qty or 0)
            if amount <= 0:
                raise LiveTradingError("Invalid qty")
            order_type = f"{sd}-market"
            if sd == "buy":
                tick = self.get_ticker(symbol=symbol)
                last = float(tick.get("close") or tick.get("price") or tick.get("lastPrice") or 0)
                if last <= 0:
                    raise LiveTradingError("HTX spot market buy requires latest price for qty->value conversion")
                amount = amount * last
            body = {
                "account-id": account_id,
                "symbol": to_htx_spot_symbol(symbol),
                "type": order_type,
                "amount": f"{amount:.12f}".rstrip("0").rstrip("."),
                "source": "spot-api",
            }
            formatted_client_order_id = self._format_spot_client_order_id(client_order_id)
            if formatted_client_order_id:
                body["client-order-id"] = formatted_client_order_id
            raw = self._spot_private_request("POST", "/v1/order/orders/place", json_body=body)
            data = raw.get("data")
            oid = str(data or "")
            return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

        contract_code = to_htx_contract_code(symbol)
        volume = self._base_to_contracts(symbol=symbol, qty=qty)
        if volume <= 0:
            raise LiveTradingError("Invalid HTX swap volume")
        sd = str(side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")
        offset = "close" if reduce_only else "open"
        lever_rate = int(self._lever_cache.get(contract_code) or 5)
        body: Dict[str, Any] = {
            "contract_code": contract_code,
            "volume": volume,
            "direction": sd,
            "offset": offset,
            "lever_rate": lever_rate,
            "order_price_type": "opponent",
        }
        if self.broker_id:
            body["channel_code"] = self.broker_id
        swap_coid = self._format_swap_client_order_id(client_order_id)
        if swap_coid is not None:
            body["client_order_id"] = swap_coid
        return self._place_swap_order(body)

    def _place_swap_order(self, body: Dict[str, Any]) -> LiveOrderResult:
        """Try isolated → cross order endpoints (isolated first to avoid 6002 on accounts
        that have been migrated away from multi-asset collateral)."""
        acct = self._detect_account_type()
        if acct == 2:
            raise LiveTradingError(
                "HTX multi-asset collateral mode (联合保证金) is active and does not support API ordering. "
                "Auto-switch to single-currency margin failed (may have open positions/orders). "
                "Please switch to 单币种保证金 in HTX web/app → Contract → Settings, then retry."
            )
        cross_body = dict(body)
        cross_body["margin_account"] = "USDT"
        order_endpoints = [
            ("v1-isolated", "/linear-swap-api/v1/swap_order", body),
            ("v1-cross", "/linear-swap-api/v1/swap_cross_order", cross_body),
        ]
        last_err: Optional[Exception] = None
        for tag, path, req_body in order_endpoints:
            try:
                raw = self._swap_private_request("POST", path, json_body=req_body)
                data = raw.get("data") or {}
                oid = str(data.get("order_id_str") or data.get("order_id") or "")
                logger.info("HTX order succeeded via %s, order_id=%s", tag, oid)
                return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)
            except LiveTradingError as e:
                logger.info("HTX %s order failed: %s", tag, e)
                last_err = e
        raise last_err or LiveTradingError("HTX all order endpoints failed")

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        reduce_only: bool = False,
        pos_side: str = "",
        client_order_id: Optional[str] = None,
    ) -> LiveOrderResult:
        px = float(price or 0)
        qty = float(size or 0)
        if px <= 0 or qty <= 0:
            raise LiveTradingError("Invalid size/price")
        sd = str(side or "").strip().lower()
        if sd not in ("buy", "sell"):
            raise LiveTradingError(f"Invalid side: {side}")

        if self.market_type == "spot":
            account_id = self._get_spot_account_id()
            body = {
                "account-id": account_id,
                "symbol": to_htx_spot_symbol(symbol),
                "type": f"{sd}-limit",
                "amount": f"{qty:.12f}".rstrip("0").rstrip("."),
                "price": f"{px:.12f}".rstrip("0").rstrip("."),
                "source": "spot-api",
            }
            formatted_client_order_id = self._format_spot_client_order_id(client_order_id)
            if formatted_client_order_id:
                body["client-order-id"] = formatted_client_order_id
            raw = self._spot_private_request("POST", "/v1/order/orders/place", json_body=body)
            data = raw.get("data")
            oid = str(data or "")
            return LiveOrderResult(exchange_id="htx", exchange_order_id=oid, filled=0.0, avg_price=0.0, raw=raw)

        contract_code = to_htx_contract_code(symbol)
        volume = self._base_to_contracts(symbol=symbol, qty=qty)
        lever_rate = int(self._lever_cache.get(contract_code) or 5)
        body: Dict[str, Any] = {
            "contract_code": contract_code,
            "volume": volume,
            "direction": sd,
            "offset": "close" if reduce_only else "open",
            "lever_rate": lever_rate,
            "price": px,
            "order_price_type": "limit",
        }
        if self.broker_id:
            body["channel_code"] = self.broker_id
        swap_coid = self._format_swap_client_order_id(client_order_id)
        if swap_coid is not None:
            body["client_order_id"] = swap_coid
        return self._place_swap_order(body)

    def cancel_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        if self.market_type == "spot":
            if order_id:
                return self._spot_private_request("POST", f"/v1/order/orders/{str(order_id)}/submitcancel")
            if client_order_id:
                return self._spot_private_request("POST", "/v1/order/orders/submitCancelClientOrder", json_body={"client-order-id": str(client_order_id)})
            raise LiveTradingError("HTX cancel_order requires order_id or client_order_id")

        body: Dict[str, Any] = {"contract_code": to_htx_contract_code(symbol)}
        if order_id:
            body["order_id"] = str(order_id)
        elif client_order_id:
            body["client_order_id"] = str(client_order_id)
        else:
            raise LiveTradingError("HTX cancel_order requires order_id or client_order_id")
        return self._swap_private_request("POST", "/linear-swap-api/v1/swap_cancel", json_body=body)

    def get_order(self, *, symbol: str, order_id: str = "", client_order_id: str = "") -> Dict[str, Any]:
        if self.market_type == "spot":
            if order_id:
                raw = self._spot_private_request("GET", f"/v1/order/orders/{str(order_id)}")
                data = raw.get("data") if isinstance(raw, dict) else {}
                return data if isinstance(data, dict) else {}
            if client_order_id:
                raw = self._spot_private_request("GET", "/v1/order/orders/getClientOrder", params={"clientOrderId": str(client_order_id)})
                data = raw.get("data") if isinstance(raw, dict) else {}
                return data if isinstance(data, dict) else {}
            raise LiveTradingError("HTX get_order requires order_id or client_order_id")

        body: Dict[str, Any] = {"contract_code": to_htx_contract_code(symbol)}
        if order_id:
            body["order_id"] = str(order_id)
        elif client_order_id:
            body["client_order_id"] = str(client_order_id)
        else:
            raise LiveTradingError("HTX get_order requires order_id or client_order_id")
        raw = self._swap_private_request("POST", "/linear-swap-api/v1/swap_order_info", json_body=body)
        data = raw.get("data") or []
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return {}

    def wait_for_fill(
        self,
        *,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
        max_wait_sec: float = 3.0,
        poll_interval_sec: float = 0.5,
    ) -> Dict[str, Any]:
        end_ts = time.time() + float(max_wait_sec or 0.0)
        last: Dict[str, Any] = {}
        while True:
            timed_out = time.time() >= end_ts
            try:
                last = self.get_order(symbol=symbol, order_id=str(order_id or ""), client_order_id=str(client_order_id or "")) or {}
            except Exception:
                last = last or {}

            filled = 0.0
            avg_price = 0.0
            fee = 0.0
            fee_ccy = "USDT"
            status = str(last.get("status") or last.get("state") or "")
            try:
                filled = float(
                    last.get("field-amount") or
                    last.get("filled_amount") or
                    last.get("trade_volume") or
                    last.get("trade_volume_avg") or
                    0.0
                )
            except Exception:
                filled = 0.0
            try:
                avg_price = float(
                    last.get("field-cash-amount") or 0.0
                )
                if filled > 0 and avg_price > 0:
                    avg_price = avg_price / filled
                else:
                    avg_price = float(last.get("field-avg-price") or last.get("trade_avg_price") or last.get("price") or 0.0)
            except Exception:
                avg_price = 0.0
            try:
                fee = abs(float(last.get("fee") or last.get("trade_fee") or 0.0))
            except Exception:
                fee = 0.0
            fee_ccy = str(last.get("fee_asset") or last.get("fee_currency") or fee_ccy or "").strip() or "USDT"

            # Do not treat "submitted" as terminal — order may not be filled yet.
            if filled > 0 and avg_price > 0:
                if fee <= 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if str(status).lower() in ("filled", "partial-filled", "canceled", "cancelled", "6", "7"):
                if fee <= 0 and filled > 0 and avg_price > 0 and not timed_out:
                    time.sleep(float(poll_interval_sec or 0.5))
                    continue
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            if timed_out:
                return {"filled": filled, "avg_price": avg_price, "fee": fee, "fee_ccy": fee_ccy, "status": status, "order": last}
            time.sleep(float(poll_interval_sec or 0.5))
