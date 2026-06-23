import os
import re
import logging
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
AWAITING_TAGS, CONFIRMING = range(2)

# Splitwise client (initialized once)
sw = None


def get_splitwise() -> SplitwiseClient:
    global sw
    if sw is None:
        sw = SplitwiseClient()
        sw.get_current_user()
        sw.find_kalash()
    return sw


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
    else:
        # Parse "mine: 1,2" and "kalash: 3,4" from the message
        mine_indices = []
        kalash_indices = []

        mine_match = re.search(r"mine\s*:\s*([\d,\s]+)", text)
        kalash_match = re.search(r"kalash\s*:\s*([\d,\s]+)", text)

        if mine_match:
            mine_indices = [int(x.strip()) for x in mine_match.group(1).split(",") if x.strip().isdigit()]
        if kalash_match:
            kalash_indices = [int(x.strip()) for x in kalash_match.group(1).split(",") if x.strip().isdigit()]

        if not mine_match and not kalash_match:
            await update.message.reply_text(
                "Couldn't understand that. Reply like:\n"
                "`mine: 1,2`\n`kalash: 3`\n\n"
                "Or `all shared` if everything splits equally.",
                parse_mode="Markdown",
            )
            return AWAITING_TAGS

        # Validate indices
        invalid = [x for x in mine_indices + kalash_indices if x not in valid_srs]
        if invalid:
            await update.message.reply_text(
                f"Item numbers {invalid} don't exist in this order. "
                f"Valid numbers: {sorted(valid_srs)}"
            )
            return AWAITING_TAGS

        # Check for overlap
        overlap = set(mine_indices) & set(kalash_indices)
        if overlap:
            await update.message.reply_text(
                f"Items {list(overlap)} are tagged as both yours and Kalash's. Fix and resend."
            )
            return AWAITING_TAGS

    # Compute split
    split = compute_split(parsed["items"], mine_indices, kalash_indices)
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
        await update.message.reply_text("Type `ok` to log to Splitwise or `cancel` to discard.", parse_mode="Markdown")
        return CONFIRMING

    # Create Splitwise expense
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
            details=details,
        )

        await update.message.reply_text(
            f"✅ *Done!* Logged to Splitwise.\n\n"
            f"Kalash owes you *₹{split['kalash_share']:.2f}*\n"
            f"Kalash will get a notification from Splitwise.",
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Splitwise error: {e}")
        await update.message.reply_text(
            f"❌ Splitwise error: {e}\n\nCheck your API key and try again."
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
    main()
