import os
import logging
import asyncio
import gspread
from threading import Thread
from datetime import datetime
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from oauth2client.service_account import ServiceAccountCredentials

# === CONFIGURAÇÕES ===
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID = "1ShIhn1IQj8txSUshTJh_ypmzoyvIO40HLNi1ZN28rIo"
ABA_NOME = "Página1"
CRED_FILE = "/etc/secrets/credentials.json"  # Ajuste conforme seu path no Render

# === ESTADOS DO CONVERSATIONHANDLER ===
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS, AWAIT_DELETION, CONFIRM_DELETION = range(5)

# === LOGGING ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# === GOOGLE SHEETS ===
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_NOME)

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("📋 Listar Produtos")],
        [KeyboardButton("❌ Excluir Produto"), KeyboardButton("ℹ️ Ajuda")]
    ]
    await update.message.reply_text(
        "Bem-vindo ao Bot de Compras! Escolha uma opção:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return MAIN_MENU

async def ask_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Digite o nome do produto:")
    return AWAIT_PRODUCT

async def ask_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["produto"] = update.message.text
    await update.message.reply_text("Digite a quantidade e unidade (ex: 2 kg):")
    return AWAIT_DETAILS

async def save_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    produto = context.user_data["produto"]
    detalhes = update.message.text
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    try:
        sheet = get_sheet()
        sheet.append_row([produto, detalhes, timestamp])
        await update.message.reply_text(f"✅ Produto '{produto}' adicionado com sucesso!")
    except Exception as e:
        logging.error(f"Erro ao salvar produto: {e}")
        await update.message.reply_text("❌ Erro ao salvar produto. Tente novamente mais tarde.")
    
    return MAIN_MENU

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        records = sheet.get_all_values()
        if len(records) <= 1:
            await update.message.reply_text("A lista está vazia.")
        else:
            produtos = "\n".join([f"• {row[0]} - {row[1]}" for row in records[1:]])
            await update.message.reply_text(f"📝 Lista de Produtos:\n\n{produtos}")
    except Exception as e:
        logging.error(f"Erro ao listar produtos: {e}")
        await update.message.reply_text("❌ Erro ao acessar a lista.")
    
    return MAIN_MENU

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        registros = sheet.get_all_values()
        nomes = [row[0] for row in registros[1:]]
        if not nomes:
            await update.message.reply_text("❌ A lista está vazia.")
            return MAIN_MENU
        await update.message.reply_text("Digite o nome exato do produto que deseja excluir:")
        context.user_data["nomes"] = nomes
        return AWAIT_DELETION
    except Exception as e:
        logging.error(f"Erro ao iniciar exclusão: {e}")
        await update.message.reply_text("❌ Erro ao acessar a planilha.")
        return MAIN_MENU

async def confirm_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.message.text.strip()
    context.user_data["para_deletar"] = nome
    if nome not in context.user_data.get("nomes", []):
        await update.message.reply_text("Produto não encontrado na lista.")
        return MAIN_MENU
    await update.message.reply_text(f"Tem certeza que deseja excluir '{nome}'? (Sim/Não)")
    return CONFIRM_DELETION

async def execute_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resposta = update.message.text.lower()
    nome = context.user_data.get("para_deletar", "")
    if resposta in ["sim", "s"]:
        try:
            sheet = get_sheet()
            registros = sheet.get_all_values()
            for idx, row in enumerate(registros):
                if row[0] == nome:
                    sheet.delete_rows(idx + 1)
                    await update.message.reply_text(f"✅ '{nome}' removido com sucesso.")
                    return MAIN_MENU
            await update.message.reply_text("❌ Produto não encontrado.")
        except Exception as e:
            logging.error(f"Erro ao deletar: {e}")
            await update.message.reply_text("❌ Erro ao excluir o produto.")
    else:
        await update.message.reply_text("❌ Exclusão cancelada.")
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Envie /start para voltar ao menu principal.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operação cancelada.")
    return MAIN_MENU

# === CONVERSATION HANDLER ===
def build_conv_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_name),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^❌ Excluir Produto$"), delete_product),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command),
            ],
            AWAIT_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_product_details)],
            AWAIT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_product)],
            AWAIT_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_deletion)],
            CONFIRM_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_deletion)],
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancelar$"), cancel)],
    )

# === FLASK + WEBHOOK SETUP ===
app = Flask(__name__)
application = None
loop = None  # Event loop global/shared

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)
        # Agendar processamento do update no loop global
        loop.call_soon_threadsafe(asyncio.create_task, application.process_update(update))
        return "OK", 200
    except Exception as e:
        logging.error(f"Erro no webhook: {e}")
        return "Erro", 500

@app.route("/")
def home():
    return "🤖 Bot de Lista de Compras está no ar!", 200

@app.route("/healthz")
def health_check():
    return "OK", 200

async def start_bot():
    global application
    application = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)
        .build()
    )
    application.add_handler(build_conv_handler())

    await application.initialize()
    webhook_url = f"{os.environ['RENDER_EXTERNAL_URL']}/webhook"
    await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logging.info(f"Webhook configurado: {webhook_url}")

    await application.start()
    while True:
        await asyncio.sleep(3600)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    Thread(target=run_flask).start()
    try:
        loop.run_until_complete(start_bot())
    except KeyboardInterrupt:
        logging.info("Bot encerrado.")
    except Exception as e:
        logging.error(f"Erro fatal: {e}")
    finally:
        loop.close()
