import os
import re
import logging
import asyncio
import tempfile
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from invoice_parser import parse_zepto_invoice, format_item_list, compute_split, format_split_summary
from splitwise_client import SplitwiseClient, build_expense_details
from sheets_logger import SheetsLogger, log_order_to_sheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
AWAITING_TAGS, CONFIRMING = range(2)

# Splitwise client (initialized once)
sw = None
sheets = None


def get_splitwise() -> SplitwiseClient:
    global sw
    if sw is None:
        sw = SplitwiseClient()
        sw.get_current_user()
        sw.find_kalash()
    return sw


def get_sheets() -> SheetsLogger | None:
    """Initialize Google Sheets logger. Returns None if not configured."""
    global sheets
    if sheets is None and os.environ.get("GOOGLE_SHEET_ID"):
        try:
            sheets = SheetsLogger()
            sheets.ensure_headers()
            logger.info("Google Sheets logger initialized")
        except Exception as e:
            logger.error(f"Failed to init Sheets logger: {e}")
            return None
    return sheets


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hey! Send me a Zepto invoice PDF and I'll help split it with Kalash.\n\n"
        "Just download the invoice from the Zepto app and forward it here."
    )


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming PDF document."""
    document = update.message.document

    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("That doesn't look like a PDF. Send the Zepto invoice PDF.")
        return ConversationHandler.END

    await update.message.reply_text("📄 Got it, parsing the invoice...")

    # Download the PDF to a temp file
    file = await document.get_file()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        parsed = parse_zepto_invoice(tmp_path)
    except Exception as e:
        logger.error(f"Parse error: {e}")
        await update.message.reply_text(
            "❌ Couldn't parse this invoice. Make sure it's a Zepto order invoice PDF."
        )
        os.unlink(tmp_path)
        return ConversationHandler.END
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not parsed["items"]:
        await update.message.reply_text("No items found in the invoice. Is this the right PDF?")
        return ConversationHandler.END

    # Store parsed data in conversation context
    context.user_data["parsed"] = parsed

    # Send formatted item list
    msg = format_item_list(parsed)
    await update.message.reply_text(msg, parse_mode="Markdown")

    return AWAITING_TAGS


async def handle_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's mine/kalash tag reply."""
    text = update.message.text.strip().lower()
    parsed = context.user_data.get("parsed")

    if not parsed:
        await update.message.reply_text("No pending order. Send a new invoice PDF.")
        return ConversationHandler.END

    valid_srs = {item["sr"] for item in parsed["items"]}

    # Handle "all shared" shortcut
    if text == "all shared":
        mine_indices = []
        kalash_indices = []
        abhirag_indices = []
    else:
        # Parse "mine: 1,2", "kalash: 3,4", "abhirag: 5" from the message
        mine_indices = []
        kalash_indices = []
        abhirag_indices = []

        mine_match = re.search(r"mine\s*:\s*([\d,\s]+)", text)
        kalash_match = re.search(r"kalash\s*:\s*([\d,\s]+)", text)
        abhirag_match = re.search(r"abhirag\s*:\s*([\d,\s]+)", text)

        if mine_match:
            mine_indices = [int(x.strip()) for x in mine_match.group(1).split(",") if x.strip().isdigit()]
        if kalash_match:
            kalash_indices = [int(x.strip()) for x in kalash_match.group(1).split(",") if x.strip().isdigit()]
        if abhirag_match:
            abhirag_indices = [int(x.strip()) for x in abhirag_match.group(1).split(",") if x.strip().isdigit()]

        if not mine_match and not kalash_match and not abhirag_match:
            await update.message.reply_text(
                "Couldn't understand that. Reply like:\n"
                "`mine: 1,2`\n`kalash: 3`\n`abhirag: 4`\n\n"
                "Or `all shared` if everything splits equally.",
                parse_mode="Markdown",
            )
            return AWAITING_TAGS

        # Validate indices
        all_tagged = mine_indices + kalash_indices + abhirag_indices
        invalid = [x for x in all_tagged if x not in valid_srs]
        if invalid:
            await update.message.reply_text(
                f"Item numbers {invalid} don't exist in this order. "
                f"Valid numbers: {sorted(valid_srs)}"
            )
            return AWAITING_TAGS

        # Check for overlap
        all_sets = [set(mine_indices), set(kalash_indices), set(abhirag_indices)]
        for i, (a, b) in enumerate([(0,1), (0,2), (1,2)]):
            overlap = all_sets[a] & all_sets[b]
            if overlap:
                names = ["yours", "Kalash's", "Abhirag's"]
                await update.message.reply_text(
                    f"Items {list(overlap)} are tagged as both {names[a]} and {names[b]}. Fix and resend."
                )
                return AWAITING_TAGS

    # Compute split
    split = compute_split(parsed["items"], mine_indices, kalash_indices, abhirag_indices)
    context.user_data["split"] = split

    # Show summary and ask for confirmation
    msg = format_split_summary(split, parsed.get("order_date", ""))
    await update.message.reply_text(msg, parse_mode="Markdown")

    return CONFIRMING


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ok/cancel confirmation."""
    text = update.message.text.strip().lower()
    parsed = context.user_data.get("parsed")
    split = context.user_data.get("split")

    if text == "cancel":
        context.user_data.clear()
        await update.message.reply_text("❌ Discarded. Send another invoice whenever.")
        return ConversationHandler.END

    if text != "ok":
        await update.message.reply_text("Type `ok` to confirm or `cancel` to discard.", parse_mode="Markdown")
        return CONFIRMING

    confirm_lines = []
    needs_splitwise = split["kalash_share"] > 0 or split.get("abhirag_share", 0) > 0

    # Only create Splitwise expense if someone owes money
    if needs_splitwise:
        await update.message.reply_text("⏳ Logging to Splitwise...")

        try:
            sw_client = get_splitwise()
            description = f"Zepto — {parsed.get('order_date', 'order')}"
            details = build_expense_details(split)

            result = sw_client.create_expense(
                description=description,
                total_cost=split["order_total"],
                my_share=split["my_share"],
                kalash_share=split["kalash_share"],
                abhirag_share=split.get("abhirag_share", 0),
                details=details,
            )

            confirm_lines.append(f"✅ *Done!* Logged to Splitwise.\n")
            if split["kalash_share"] > 0:
                confirm_lines.append(f"Kalash owes you *₹{split['kalash_share']:.2f}*")
            if split.get("abhirag_share", 0) > 0:
                confirm_lines.append(f"Abhirag owes you *₹{split['abhirag_share']:.2f}*")

        except Exception as e:
            logger.error(f"Splitwise error: {e}")
            confirm_lines.append(f"❌ Splitwise error: {e}")
    else:
        confirm_lines.append(f"✅ *All yours — ₹{split['order_total']:.2f}*")
        confirm_lines.append(f"No split needed, skipped Splitwise.")

    # Log to Google Sheets (if configured) — always, even for personal orders
    sheets_client = get_sheets()
    if sheets_client:
        try:
            row_count = log_order_to_sheets(sheets_client, parsed, split)
            confirm_lines.append(f"\n📊 {row_count} rows logged to analytics sheet.")
        except Exception as e:
            logger.error(f"Sheets logging failed: {e}")
            confirm_lines.append(f"⚠️ Sheets logging failed — check logs.")

    await update.message.reply_text(
        "\n".join(confirm_lines),
        parse_mode="Markdown",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current operation."""
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send a new invoice whenever.")
    return ConversationHandler.END


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Document.PDF, handle_pdf),
        ],
        states={
            AWAITING_TAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tags)],
            CONFIRMING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    # Check if we're on Render (webhook mode) or local (polling mode)
    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if render_url:
        # Webhook mode for Render
        port = int(os.environ.get("PORT", 10000))

        # RENDER_EXTERNAL_URL may or may not include https://
        if render_url.startswith("https://") or render_url.startswith("http://"):
            webhook_url = f"{render_url}/webhook"
        else:
            webhook_url = f"https://{render_url}/webhook"

        logger.info(f"Starting webhook on port {port}, URL: {webhook_url}")

        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url,
        )
    else:
        # Polling mode for local development
        logger.info("Starting in polling mode (local dev)")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    # Ensure event loop exists (needed for Python 3.14+)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
