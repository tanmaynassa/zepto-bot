import pdfplumber
import re
from datetime import datetime


def parse_zepto_invoice(pdf_path: str) -> dict:
    """
    Parse a Zepto invoice PDF and extract order items.
    Handles multi-page invoices where items spill onto page 2+.
    Returns dict with order_date, order_no, items list, and total.
    """
    with pdfplumber.open(pdf_path) as pdf:
        all_tables = []
        for page in pdf.pages:
            all_tables.extend(page.extract_tables())

    if len(all_tables) < 2:
        raise ValueError("Could not find item table in invoice PDF")

    # Extract items from all tables across all pages
    items = []
    for table in all_tables:
        for row in table:
            # Find the SR No — could be at index 0 or 1 depending on page layout
            sr_no = None
            sr_idx = None
            for idx in range(min(3, len(row))):
                cell = (row[idx] or "").strip()
                if cell.isdigit():
                    sr_no = cell
                    sr_idx = idx
                    break

            if sr_no is None:
                continue

            # Item name is the next column after SR No
            name_idx = sr_idx + 1
            if name_idx >= len(row):
                continue

            item_name = (row[name_idx] or "").replace("\n", " ").strip()
            item_name = re.sub(r"\s+", " ", item_name)

            if not item_name:
                continue

            try:
                total_amt = float(row[-1])
            except (ValueError, TypeError):
                continue

            # Avoid duplicate SR numbers
            if not any(i["sr"] == int(sr_no) for i in items):
                items.append({
                    "sr": int(sr_no),
                    "name": item_name,
                    "amount": total_amt,
                })

    # Extract date and order number from header table
    header_text = all_tables[0][0][0] if all_tables[0] else ""
    order_date = ""
    order_no = ""

    date_match = re.search(r"Date\s*:\s*([\d\-]+)", header_text)
    if date_match:
        order_date = date_match.group(1)

    order_match = re.search(r"Order No\.?:\s*(\S+)", header_text)
    if order_match:
        order_no = order_match.group(1)

    total = sum(item["amount"] for item in items)

    return {
        "order_date": order_date,
        "order_no": order_no,
        "items": items,
        "total": total,
    }


def format_item_list(parsed: dict) -> str:
    """Format parsed invoice into a readable numbered list for Telegram."""
    lines = [f"🛒 *Zepto Order — {parsed['order_date']}*"]
    lines.append(f"Total: ₹{parsed['total']:.2f}\n")

    for item in parsed["items"]:
        lines.append(f"`{item['sr']}.` {item['name']} — ₹{item['amount']:.2f}")

    lines.append("\n*Tag your items:*")
    lines.append("`mine: 1,2`")
    lines.append("`kalash: 3`")
    lines.append("`abhirag: 4`")
    lines.append("_(everything else splits 50/50 between you & Kalash)_")
    lines.append("\nOr type `all shared` if everything splits equally.")

    return "\n".join(lines)


def compute_split(items: list, mine_indices: list, kalash_indices: list, abhirag_indices: list = None) -> dict:
    """
    Compute the expense split.
    - mine_indices: item SR numbers that are Tanmay's only
    - kalash_indices: item SR numbers that are Kalash's only
    - abhirag_indices: item SR numbers that are Abhirag's only
    - everything else is shared 50/50 between Tanmay and Kalash
    
    Since Tanmay pays the full bill upfront, we compute how much each person owes.
    """
    if abhirag_indices is None:
        abhirag_indices = []

    my_total = 0.0
    kalash_total = 0.0
    abhirag_total = 0.0
    shared_total = 0.0

    my_items = []
    kalash_items = []
    abhirag_items = []
    shared_items = []

    for item in items:
        if item["sr"] in mine_indices:
            my_total += item["amount"]
            my_items.append(item)
        elif item["sr"] in kalash_indices:
            kalash_total += item["amount"]
            kalash_items.append(item)
        elif item["sr"] in abhirag_indices:
            abhirag_total += item["amount"]
            abhirag_items.append(item)
        else:
            shared_total += item["amount"]
            shared_items.append(item)

    shared_each = round(shared_total / 2, 2)  # only between Tanmay & Kalash

    my_share = round(my_total + shared_each, 2)
    kalash_share = round(kalash_total + shared_each, 2)
    abhirag_share = round(abhirag_total, 2)  # only his personal items
    order_total = round(my_total + kalash_total + abhirag_total + shared_total, 2)

    return {
        "my_items": my_items,
        "kalash_items": kalash_items,
        "abhirag_items": abhirag_items,
        "shared_items": shared_items,
        "my_personal": my_total,
        "kalash_personal": kalash_total,
        "abhirag_personal": abhirag_total,
        "shared_total": shared_total,
        "shared_each": shared_each,
        "my_share": my_share,
        "kalash_share": kalash_share,
        "abhirag_share": abhirag_share,
        "order_total": order_total,
    }


def format_split_summary(split: dict, order_date: str) -> str:
    """Format the split result into a confirmation message."""
    lines = [f"📊 *Split Summary — {order_date}*\n"]

    if split["my_items"]:
        names = ", ".join(i["name"] for i in split["my_items"])
        lines.append(f"🔹 *Your items:* ₹{split['my_personal']:.2f}")
        lines.append(f"   _{names}_\n")

    if split["kalash_items"]:
        names = ", ".join(i["name"] for i in split["kalash_items"])
        lines.append(f"🔸 *Kalash's items:* ₹{split['kalash_personal']:.2f}")
        lines.append(f"   _{names}_\n")

    if split["abhirag_items"]:
        names = ", ".join(i["name"] for i in split["abhirag_items"])
        lines.append(f"🟣 *Abhirag's items:* ₹{split['abhirag_personal']:.2f}")
        lines.append(f"   _{names}_\n")

    if split["shared_items"]:
        names = ", ".join(i["name"] for i in split["shared_items"])
        lines.append(f"🔀 *Shared (you & Kalash 50/50):* ₹{split['shared_total']:.2f} → ₹{split['shared_each']:.2f} each")
        lines.append(f"   _{names}_\n")

    lines.append(f"💰 *You paid:* ₹{split['order_total']:.2f}")
    if split["kalash_share"] > 0:
        lines.append(f"📌 *Kalash owes you:* ₹{split['kalash_share']:.2f}")
    if split["abhirag_share"] > 0:
        lines.append(f"📌 *Abhirag owes you:* ₹{split['abhirag_share']:.2f}")
    lines.append(f"\nType `ok` to log to Splitwise or `cancel` to discard.")

    return "\n".join(lines)
