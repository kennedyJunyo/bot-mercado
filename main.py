import os
import logging
import gspread
from flask import Flask, request
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton  # ✅ Importação correta
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from oauth2client.service_account import ServiceAccountCredentials

# === CONFIGURAÇÕES ===
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 10000))
SPREADSHEET_ID = "1ShIhn1IQj8txSUshTJh_ypmzoyvIO40HLNi1ZN28rIo"
ABA_NOME = "Página1"

logging.basicConfig(level=logging.INFO)

# === GOOGLE SHEETS ===
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds ",
        "https://www.googleapis.com/auth/spreadsheets ",
        "https://www.googleapis.com/auth/drive "
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name("/etc/secrets/credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(ABA_NOME)
    return sheet

# === FLASK ===
app = Flask(__name__)

@app.route("/")
def home():
    return "🛒 Bot de Compras está no ar!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    asyncio.run(application.process_update(update))
    return "OK", 200

@app.route("/healthz")
def healthz():
    return "OK", 200

# === TECLADOS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("❌ Excluir Produto")],
        [KeyboardButton("📋 Listar Produtos")],
        [KeyboardButton("ℹ️ Ajuda")]
    ], resize_keyboard=True)

cancel_keyboard = ReplyKeyboardMarkup([[KeyboardButton("❌ Cancelar")]], resize_keyboard=True)

# === ESTADOS ===
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS, AWAIT_DELETION, CONFIRM_DELETION = range(5)

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Bot de Compras Compartilhado*\n\nEscolha uma opção:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠️ *Ajuda*\n\n"
        "• ➕ Adicionar Produto: Cadastra um item\n"
        "• ❌ Excluir Produto: Remove um item\n"
        "• 📋 Listar Produtos: Mostra os itens\n\n"
        "📌 Exemplo de entrada:\n"
        "`Mussarela`\n"
        "`0.5 kg 25.00`",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

async def ask_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Qual produto deseja adicionar?", reply_markup=cancel_keyboard)
    return AWAIT_PRODUCT

async def ask_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["produto"] = update.message.text.strip().title()
    await update.message.reply_text("✏️ Agora envie os detalhes no formato:\n`quantidade unidade preço`\n\nEx: `0.5 kg 25.00`", parse_mode="Markdown")
    return AWAIT_DETAILS

async def save_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    produto = context.user_data.get("produto")
    usuario = update.message.from_user.username or update.message.from_user.first_name or "anônimo"

    try:
        quantidade, unidade, preco = update.message.text.strip().split()
        preco = float(preco)
        categoria = "Frios" if "kg" in unidade.lower() else "Outros"

        sheet = get_sheet()
        sheet.append_row([
            produto, categoria, unidade, f"{preco:.2f}",
            datetime.now().strftime("%Y-%m-%d"), f"@{usuario}"
        ])
        await update.message.reply_text(f"✅ *{produto}* adicionado com sucesso!", parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao salvar produto: {e}")
        await update.message.reply_text("⚠️ Formato inválido. Use: `0.5 kg 25.00`", parse_mode="Markdown")
        return AWAIT_DETAILS

    return MAIN_MENU

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        dados = sheet.get_all_records()
        if not dados:
            await update.message.reply_text("📭 Nenhum produto na lista ainda.")
            return MAIN_MENU

        texto = "📋 *Lista de Produtos:*\n\n"
        for linha in dados[-10:]:  # últimos 10 registros
            texto += f"🔹 *{linha['Produto']}* — R${linha['Preço']} ({linha['Unidade']})\n"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao listar: {e}")
        await update.message.reply_text("❌ Erro ao acessar a lista.")
    return MAIN_MENU

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        dados = sheet.get_all_values()
        nomes = list({row[0] for row in dados[1:] if row[0]})  # Produto
        if not nomes:
            await update.message.reply_text("❌ Lista vazia.")
            return MAIN_MENU

        botoes = [[KeyboardButton(nome)] for nome in nomes]
        botoes.append([KeyboardButton("❌ Cancelar")])
        await update.message.reply_text("🗑️ Qual produto deseja excluir?", reply_markup=ReplyKeyboardMarkup(botoes, resize_keyboard=True))
        return AWAIT_DELETION
    except Exception as e:
        logging.error(f"Erro ao carregar nomes para exclusão: {e}")
        await update.message.reply_text("❌ Erro ao acessar a lista.")
        return MAIN_MENU

async def confirm_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    produto = update.message.text.strip()
    context.user_data["delete"] = produto
    await update.message.reply_text(f"⚠️ Confirmar exclusão de *{produto}*? (responda com SIM ou NÃO)", parse_mode="Markdown")
    return CONFIRM_DELETION

async def execute_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resposta = update.message.text.strip().lower()
    if resposta != "sim":
        await update.message.reply_text("❌ Exclusão cancelada.", reply_markup=main_menu_keyboard())
        return MAIN_MENU

    try:
        produto = context.user_data.get("delete")
        sheet = get_sheet()
        valores = sheet.get_all_values()

        for idx, row in enumerate(valores[1:], start=2):  # pula cabeçalho
            if row[0].lower() == produto.lower():
                sheet.delete_row(idx)
                await update.message.reply_text(f"🗑️ *{produto}* excluído.", parse_mode="Markdown", reply_markup=main_menu_keyboard())
                return MAIN_MENU

        await update.message.reply_text("❌ Produto não encontrado.")
    except Exception as e:
        logging.error(f"Erro ao excluir: {e}")
        await update.message.reply_text("❌ Erro ao tentar excluir.")
    return MAIN_MENU

# === INICIALIZAÇÃO DO BOT ===
async def main():
    global application

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_name),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^❌ Excluir Produto$"), delete_product),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command)
            ],
            AWAIT_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_product_details)],
            AWAIT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_product)],
            AWAIT_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_deletion)],
            CONFIRM_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_deletion)]
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancelar$"), cancel)]
    )

    application.add_handler(conv_handler)

    # Configura webhook no Telegram
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL", "https://bot-mercado.onrender.com ") + "/webhook"
    await application.bot.set_webhook(url=webhook_url)

    # Inicia Flask
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
