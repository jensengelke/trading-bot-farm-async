"""
Framework Message Formatters for Telegram Notifications.

Generates clean HTML-formatted strings for Telegram parse_mode='HTML'.
Used across Telegram bot, DAX spread observation bot, and others.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import pytz
from ib_async import Trade, Fill, Contract
from framework.telegram_client import escape_html


def get_ny_now() -> str:
    """Current timestamp in America/New_York timezone."""
    tz = pytz.timezone("America/New_York")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def get_berlin_now() -> str:
    """Current timestamp in Europe/Berlin timezone."""
    tz = pytz.timezone("Europe/Berlin")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def format_contract_summary(contract: Optional[Contract]) -> str:
    """Produce concise, human-readable summary of a contract."""
    if not contract:
        return "Unknown Contract"
    sec_type = contract.secType or ""
    symbol = contract.symbol or ""
    if sec_type == "BAG":
        num = len(contract.comboLegs) if contract.comboLegs else 0
        return f"{symbol} Combo ({num} legs)"
    elif sec_type == "OPT":
        return f"{symbol} {contract.lastTradeDateOrContractMonth or ''} {contract.strike or ''}{contract.right or ''}"
    elif sec_type == "STK":
        return f"{symbol} (Stock)"
    elif sec_type == "FUT":
        return f"{symbol} Future {contract.lastTradeDateOrContractMonth or ''}"
    elif sec_type == "IND":
        return f"{symbol} (Index)"
    return f"{symbol} ({sec_type})"


def format_connection_state_change(
    connected: bool,
    host: str = "",
    port: int = 0,
    account: str = "",
    details: str = ""
) -> str:
    """Format Telegram alert for IB connection status changes."""
    status_icon = "🟢" if connected else "🔴"
    status_title = "IBKR Connected & Online" if connected else "IBKR Disconnected!"
    
    lines = [
        f"{status_icon} <b>{status_title}</b>",
    ]
    if account:
        lines.append(f"<b>Account:</b> <code>{escape_html(account)}</code>")
    if host and port:
        lines.append(f"<b>Gateway:</b> <code>{escape_html(host)}:{port}</code>")
    if details:
        lines.append(f"<b>Details:</b> {escape_html(details)}")
    lines.append(f"<b>Time:</b> {escape_html(get_ny_now())}")
    return "\n".join(lines)


def format_order_placement(trade: Trade, account: str = "") -> str:
    """Format Telegram alert for order placement."""
    order = trade.order
    contract = trade.contract
    acct = account or getattr(order, "account", "") or ""
    lmt = getattr(order, "lmtPrice", None)
    price_str = f" @ {lmt:.2f}" if lmt is not None else ""
    order_ref = order.orderRef or "N/A"
    
    msg = [
        "🟢 <b>Order Submitted</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(contract))}",
        f"<b>Action:</b> {order.action} {order.totalQuantity} {order.orderType}{price_str}",
        f"<b>Order ID:</b> <code>{order.orderId}</code>",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
    ]
    if contract.secType == "BAG" and contract.comboLegs:
        msg.append("<b>Legs:</b>")
        for i, leg in enumerate(contract.comboLegs, start=1):
            act = "BUY" if leg.action == "BUY" else "SELL"
            msg.append(f"  • #{i}: {act} {leg.ratio}x conId {leg.conId}")
    msg.append(f"<b>Time:</b> {escape_html(get_ny_now())}")
    return "\n".join(msg)


def format_order_fill(trade: Trade, fill: Fill, account: str = "") -> str:
    """Format Telegram alert for execution fill with commission."""
    order = trade.order
    contract = trade.contract
    exec_info = fill.execution
    acct = account or getattr(order, "account", "") or ""
    order_ref = order.orderRef or "N/A"
    status = trade.orderStatus.status if trade.orderStatus else "Filled"
    filled = trade.orderStatus.filled if trade.orderStatus else exec_info.shares
    total = (trade.orderStatus.remaining + filled) if trade.orderStatus else exec_info.shares
    
    comm_str = ""
    if fill.commissionReport and fill.commissionReport.commission is not None:
        comm = fill.commissionReport.commission
        curr = fill.commissionReport.currency or "USD"
        comm_str = f"\n<b>Commission:</b> {comm:.2f} {escape_html(curr)}"
        
    msg = [
        "🎯 <b>Order Executed</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(contract))}",
        f"<b>Fill:</b> {exec_info.side} {exec_info.shares} @ {exec_info.price:.2f}",
        f"<b>Progress:</b> {filled} / {total} ({status})",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
        f"<b>Order ID:</b> <code>{order.orderId}</code>{comm_str}",
        f"<b>Time:</b> {escape_html(get_ny_now())}"
    ]
    return "\n".join(msg)


def format_order_cancelled(trade: Trade, account: str = "") -> str:
    """Format Telegram alert for cancelled order."""
    order = trade.order
    contract = trade.contract
    acct = account or getattr(order, "account", "") or ""
    order_ref = order.orderRef or "N/A"
    status = trade.orderStatus.status if trade.orderStatus else "Cancelled"
    
    msg = [
        "🔴 <b>Order Cancelled</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(contract))}",
        f"<b>Action:</b> {order.action} {order.totalQuantity} {order.orderType}",
        f"<b>Order ID:</b> <code>{order.orderId}</code>",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
        f"<b>Status:</b> {escape_html(status)}",
        f"<b>Time:</b> {escape_html(get_ny_now())}"
    ]
    return "\n".join(msg)


def format_order_status_change(trade: Trade, old_status: str, account: str = "") -> str:
    """Format Telegram alert for significant order status transition."""
    order = trade.order
    contract = trade.contract
    acct = account or getattr(order, "account", "") or ""
    order_ref = order.orderRef or "N/A"
    new_status = trade.orderStatus.status if trade.orderStatus else "Unknown"
    
    msg = [
        "🔄 <b>Order Status Update</b>",
        f"<b>Account:</b> <code>{escape_html(acct)}</code>",
        f"<b>Contract:</b> {escape_html(format_contract_summary(contract))}",
        f"<b>Status:</b> {escape_html(old_status)} ➔ <b>{escape_html(new_status)}</b>",
        f"<b>Action:</b> {order.action} {order.totalQuantity} {order.orderType}",
        f"<b>Order ID:</b> <code>{order.orderId}</code>",
        f"<b>Order Ref:</b> <code>{escape_html(order_ref)}</code>",
        f"<b>Time:</b> {escape_html(get_ny_now())}"
    ]
    return "\n".join(msg)


def format_connected_readiness_message(
    bot_id: str,
    account: str,
    connection_name: str,
    account_summary: Optional[Dict[str, Dict[str, str]]] = None,
    positions: Optional[List[Any]] = None,
    open_trades: Optional[List[Trade]] = None,
    title: str = "Telegram Notifier Connected & Ready"
) -> str:
    """Format startup readiness summary with account balances, positions, and open orders."""
    lines = [
        f"🟢 <b>{title}</b>",
        f"<b>Instance / Bot:</b> <code>{escape_html(bot_id)}</code>",
    ]
    if account:
        lines.append(f"<b>Account:</b> <code>{escape_html(account)}</code>")
    lines.append(f"<b>Connection:</b> <code>{escape_html(connection_name)}</code>")
    lines.append(f"<b>Time:</b> {escape_html(get_ny_now())}")
    lines.append("")

    # 1. Account Balances Section
    lines.append("💰 <b>Account Balances:</b>")
    if account_summary:
        keys_to_show = [
            ("NetLiquidation", "Net Liquidation"),
            ("TotalCashValue", "Total Cash"),
            ("AvailableFunds", "Available Funds"),
            ("BuyingPower", "Buying Power"),
            ("GrossPositionValue", "Gross Position Value"),
            ("UnrealizedPnL", "Unrealized P&L")
        ]
        has_any = False
        for tag, label in keys_to_show:
            if tag in account_summary:
                item = account_summary[tag]
                val_str = item.get("value", "")
                curr = item.get("currency", "")
                try:
                    val_float = float(val_str)
                    val_fmt = f"{val_float:,.2f}"
                except (ValueError, TypeError):
                    val_fmt = val_str
                curr_suffix = f" {curr}" if curr else ""
                lines.append(f"  • <b>{escape_html(label)}:</b> {val_fmt}{escape_html(curr_suffix)}")
                has_any = True
        if not has_any:
            lines.append("  • <i>Key balance metrics unavailable</i>")
    else:
        lines.append("  • <i>Balance details not available</i>")
    lines.append("")

    # 2. Portfolio Positions Section
    pos_count = len(positions) if positions else 0
    lines.append(f"📊 <b>Portfolio Positions ({pos_count}):</b>")
    if positions:
        for pos in positions:
            contract = getattr(pos, "contract", None)
            contract_desc = format_contract_summary(contract) if contract else "Unknown"
            
            qty = getattr(pos, "position", 0)
            try:
                qty_float = float(qty)
                if qty_float > 0:
                    qty_str = f"+{int(qty_float)}" if qty_float.is_integer() else f"+{qty_float:.2f}"
                else:
                    qty_str = f"{int(qty_float)}" if qty_float.is_integer() else f"{qty_float:.2f}"
            except (ValueError, TypeError):
                qty_str = str(qty)

            avg_cost = getattr(pos, "avgCost", None)
            if avg_cost is None:
                avg_cost = getattr(pos, "averageCost", 0.0)
            try:
                avg_cost_str = f"{float(avg_cost):.2f}"
            except (ValueError, TypeError):
                avg_cost_str = str(avg_cost)

            mkt_price = getattr(pos, "marketPrice", None)
            unrealized_pnl = getattr(pos, "unrealizedPNL", None)
            
            extra = []
            if mkt_price is not None and mkt_price > 0:
                extra.append(f"Mkt: {mkt_price:.2f}")
            if unrealized_pnl is not None:
                pnl_sign = "+" if unrealized_pnl >= 0 else ""
                extra.append(f"P&L: {pnl_sign}${unrealized_pnl:,.2f}")
                
            extra_str = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"  • <b>{escape_html(contract_desc)}:</b> {qty_str} @ {avg_cost_str}{escape_html(extra_str)}")
    else:
        lines.append("  • <i>No open positions</i>")
    lines.append("")

    # 3. Open Orders Section
    open_count = len(open_trades) if open_trades else 0
    lines.append(f"📋 <b>Open Orders ({open_count}):</b>")
    if open_trades:
        for trade in open_trades:
            order = trade.order
            contract = trade.contract
            contract_desc = format_contract_summary(contract)
            action = order.action
            total_qty = order.totalQuantity
            order_type = order.orderType
            lmt = getattr(order, "lmtPrice", None)
            aux = getattr(order, "auxPrice", None)
            price_str = f" @ {lmt:.2f}" if lmt is not None else (f" aux {aux:.2f}" if aux is not None else "")
            status = trade.orderStatus.status if trade.orderStatus else "Active"
            ref_str = f" | Ref: <code>{escape_html(order.orderRef)}</code>" if order.orderRef else ""
            
            lines.append(
                f"  • <b>{action} {total_qty} {escape_html(contract_desc)}</b> "
                f"({order_type}{price_str}) [{escape_html(status)}]{ref_str}"
            )
    else:
        lines.append("  • <i>No open orders</i>")

    return "\n".join(lines)



def format_daily_account_summary(
    account: str,
    account_summary: Dict[str, Dict[str, str]],
    pos_count: int,
    order_count: int
) -> str:
    """Format daily overall account health status broadcast."""
    lines = [
        "📊 <b>Daily Account Summary</b>",
        f"<b>Account:</b> <code>{escape_html(account)}</code>",
        f"<b>Time:</b> {escape_html(get_ny_now())}",
        ""
    ]
    if account_summary:
        for tag, label in [
            ("NetLiquidation", "Net Liquidation"),
            ("TotalCashValue", "Total Cash"),
            ("AvailableFunds", "Available Funds"),
            ("BuyingPower", "Buying Power"),
            ("UnrealizedPnL", "Unrealized P&L")
        ]:
            if tag in account_summary:
                item = account_summary[tag]
                val = item.get("value", "")
                curr = item.get("currency", "")
                try:
                    val_fmt = f"{float(val):,.2f}"
                except (ValueError, TypeError):
                    val_fmt = val
                lines.append(f"• <b>{label}:</b> {val_fmt} {escape_html(curr)}")
    lines.append(f"• <b>Active Positions:</b> {pos_count}")
    lines.append(f"• <b>Working Orders:</b> {order_count}")
    return "\n".join(lines)


def format_startup_message(
    bot_id: str,
    account: str,
    connection_name: str,
    account_summary: Optional[dict] = None,
    positions: Optional[list] = None,
    open_trades: Optional[list] = None
) -> str:
    """Backward-compatible alias for format_connected_readiness_message."""
    return format_connected_readiness_message(
        bot_id=bot_id,
        account=account,
        connection_name=connection_name,
        account_summary=account_summary,
        positions=positions,
        open_trades=open_trades
    )

