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
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS, AWAIT_PRICE, AWAIT_DELETION, CONFIRM_DELETION = range(6)

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

# === TECLADOS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("❌ Excluir Produto")],
        [KeyboardButton("📋 Listar Produtos"), KeyboardButton("🕒 Histórico")],
        [KeyboardButton("ℹ️ Ajuda")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Cancelar")]],
        resize_keyboard=True
    )

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Bot de Compras Inteligente* 🛒\n\nEscolha uma opção ou digite direto o nome do produto:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Operação cancelada.",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

# Permite digitar o nome do produto direto
async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    text = update.message.text.strip().title()
    context.user_data['current_product'] = text
    
    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]  # Ignora cabeçalho
    produtos = [row[0] for row in rows]
    historico = [row for row in rows if row[0] == text]

    format_message = (
        "📝 *Formato de entrada:*\n"
        "• Frios: `0.5 kg`\n"
        "• Papel Higiênico: `4 rolos 40`\n"
        "• Outros: `2 litro`"
    )
    # Se existe histórico, mostrar último preço e pergunta detalhes
    if historico:
        ultimo = historico[-1]
        preco_ultimo = float(ultimo[3]) if ultimo[3] else None
        await update.message.reply_text(
            f"📊 Último preço de {text}: R${preco_ultimo:.2f}\n\n{format_message}",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
    else:
        await update.message.reply_text(
            f"📦 Novo produto: {text}\n\n{format_message}",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
    return AWAIT_DETAILS

async def ask_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    context.user_data["detalhes"] = update.message.text
    await update.message.reply_text(
        "Digite o preço do produto (ex: 8.50):",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_PRICE

async def save_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    produto = context.user_data["current_product"]
    detalhes = context.user_data["detalhes"]
    preco_str = update.message.text.replace(",", ".")
    try:
        preco = float(preco_str)
    except ValueError:
        await update.message.reply_text("⚠️ Preço inválido, tente novamente. Exemplo: 8.50", reply_markup=cancel_keyboard())
        return AWAIT_PRICE

    detalhes_split = detalhes.split()
    categoria = "Outros"
    unidade = ""
    resumo = ""
    calculo = ""
    try:
        if "Papel Higiênico" in produto:
            rolos = float(detalhes_split[0])
            metros = float(detalhes_split[2]) if len(detalhes_split) > 2 else float(detalhes_split[1])
            categoria = "Limpeza"
            unidade = "metros"
            preco_por_metro = preco / metros if metros else 0
            preco_por_rolo = preco / rolos if rolos else 0
            resumo = f"{rolos} rolos / {metros}m - R${preco:.2f} (R${preco_por_metro:.2f}/m, R${preco_por_rolo:.2f}/rolo)"
            calculo = f"Preço por metro: R${preco_por_metro:.2f}\nPreço por rolo: R${preco_por_rolo:.2f}"
        elif any(p in produto for p in ["Queijo", "Presunto", "Mussarela", "Peito De Peru"]):
            peso = float(detalhes_split[0])
            categoria = "Frios"
            unidade = "kg"
            preco_por_kg = preco / peso if peso else 0
            resumo = f"{peso}kg - R${preco:.2f} (R${preco_por_kg:.2f}/kg)"
            calculo = f"Preço por kg: R${preco_por_kg:.2f}"
        else:
            quantidade = float(detalhes_split[0])
            unidade = detalhes_split[1] if len(detalhes_split) > 1 else ""
            resumo = f"{quantidade} {unidade} - R${preco:.2f}"
            calculo = ""
    except Exception as e:
        logging.error(f"Erro ao processar detalhes: {e}")
        await update.message.reply_text(
            "⚠️ Formato de detalhes inválido. Exemplos: '0.5 kg', '4 rolos 40', '2 litro'. Tente novamente.",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_DETAILS

    # Busca histórico do produto para comparar
    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]
    historico = [row for row in rows if row[0] == produto]
    mensagem_comparacao = ""
    if historico:
        ultimo = historico[-1]
        preco_ultimo = float(ultimo[3]) if ultimo[3] else None
        if preco_ultimo:
            if preco < preco_ultimo:
                mensagem_comparacao = f"🟢 Mais barato que a última compra (R${preco_ultimo:.2f})!"
            elif preco > preco_ultimo:
                mensagem_comparacao = f"🔴 Mais caro que a última compra (R${preco_ultimo:.2f})!"
            else:
                mensagem_comparacao = f"🟡 Mesmo preço que a última compra (R${preco_ultimo:.2f})!"

    # Salvar na planilha Google Sheets
    try:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        sheet.append_row([produto, categoria, detalhes, preco, resumo, timestamp])
        await update.message.reply_text(
            f"✅ Produto '{produto}' salvo: {resumo}\n{calculo}\n{mensagem_comparacao}",
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        logging.error(f"Erro ao salvar produto: {e}")
        await update.message.reply_text("❌ Erro ao salvar produto. Tente novamente mais tarde.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]
    produtos = set([row[0] for row in rows])
    if not produtos:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado para excluir.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    
    buttons = [[KeyboardButton(name)] for name in produtos]
    buttons.append([KeyboardButton("❌ Cancelar")])
    await update.message.reply_text(
        "🗑️ Selecione o produto a excluir:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    )
    return AWAIT_DELETION

async def confirm_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    produto = update.message.text
    sheet = get_sheet()
    rows = sheet.get_all_values()
    idx_to_delete = None
    for idx, row in enumerate(rows):
        if row[0] == produto:
            idx_to_delete = idx + 1  # 1-based index for gspread
    if idx_to_delete:
        context.user_data['product_to_delete'] = produto
        await update.message.reply_text(
            f"⚠️ Confirmar exclusão de *{produto}*\nDigite 'SIM' para confirmar ou 'NÃO' para cancelar",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✅ SIM"), KeyboardButton("❌ NÃO")]
            ], resize_keyboard=True)
        )
        return CONFIRM_DELETION
    else:
        await update.message.reply_text(
            f"ℹ️ Produto '{produto}' não encontrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

async def execute_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.upper()
    if text == "✅ SIM" or text == "SIM":
        produto = context.user_data['product_to_delete']
        sheet = get_sheet()
        rows = sheet.get_all_values()
        idx_to_delete = None
        for idx, row in enumerate(rows):
            if row[0] == produto:
                idx_to_delete = idx + 1
        if idx_to_delete:
            sheet.delete_rows(idx_to_delete)
            await update.message.reply_text(
                f"🗑️ *{produto}* foi excluído permanentemente.",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"ℹ️ Produto '{produto}' não encontrado.",
                reply_markup=main_menu_keyboard()
            )
    else:
        await update.message.reply_text(
            "❌ Exclusão cancelada.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]
    if not rows:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    message = "📋 *Lista de Produtos*\n\n"
    for row in rows:
        message += f"🏷️ *{row[0]}*\n• {row[4]}\n\n"
    await update.message.reply_text(
        message,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]
    produtos = set([row[0] for row in rows])
    if not produtos:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    buttons = [[KeyboardButton(name)] for name in produtos]
    buttons.append([KeyboardButton("❌ Cancelar")])
    await update.message.reply_text(
        "🔍 Selecione o produto para ver o histórico:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    )
    return MAIN_MENU  # ou outro estado para escolha de histórico

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛒 *Ajuda do Bot de Compras*\n\n"
        "🔹 *Como usar:*\n"
        "• Digite o nome do produto direto ou use o menu\n"
        "• Forneça detalhes e preço\n"
        "• O bot compara com o preço anterior\n"
        "• `➕ Adicionar Produto`: Cadastra novos itens\n"
        "• `❌ Excluir Produto`: Remove produtos cadastrados\n"
        "• `📋 Listar Produtos`: Mostra todos os itens\n"
        "• `🕒 Histórico`: Consulta histórico de preços\n\n"
        "📝 *Formatos de entrada:*\n"
        "• Frios: `0.5 kg` (peso e unidade)\n"
        "• Papel Higiênico: `4 rolos 40` (rolos e metros)\n"
        "• Outros: `2 litro` (quantidade e unidade)"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

# === CONVERSATION HANDLER ===
def build_conv_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), handle_product_name),
                MessageHandler(filters.Regex("^❌ Excluir Produto$"), delete_product),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^🕒 Histórico$"), show_history),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
            ],
            AWAIT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_product_price)],
            AWAIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_product)],
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
        if not application or not loop:
            logging.error("Application ou event loop não inicializados!")
            return "Loop não iniciado", 500
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)
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
