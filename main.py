import os
import json
import logging
import asyncio
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from flask import Flask, request
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# === VARIÁVEIS DE AMBIENTE ===
TOKEN                = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID       = os.environ["SPREADSHEET_ID"]
WEBHOOK_URL          = os.environ["WEBHOOK_URL"]    # ex: https://seu-app.onrender.com
PORT                 = int(os.environ.get("PORT", 5000))
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# === NOMES DAS ABAS NO SHEET ===
ABA_PRODUTOS = "Página1"
ABA_SEMANA   = "ComprasSemana"

# === ESTADOS DE CONVERSAÇÃO ===
(
    MAIN_MENU,
    AWAIT_PRODUCT_NAME,
    AWAIT_CONFIRM_UPDATE,
    AWAIT_DETAILS,
    AWAIT_PRICE,
    AWAIT_WEEKLY_MENU,
    AWAIT_WEEKLY_ADD,
    AWAIT_CONFIRM_CLEAR_WEEKLY
) = range(8)

# === LOG ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# === UTILITÁRIO PARA ABRIR UMA ABA ===
def get_sheet(worksheet_name: str):
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(worksheet_name)

# === TECLADOS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Adicionar Produto", "❌ Excluir Produto"],
        ["📋 Listar Produtos", "⏱️ Histórico"],
        ["🛒 Compras Semanais", "ℹ️ Ajuda"]
    ], resize_keyboard=True)

def yes_no_keyboard():
    return ReplyKeyboardMarkup([["Sim", "Não"]], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ Cancelar"]], resize_keyboard=True)

def weekly_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["📥 Adicionar produtos"],
        ["📋 Listar produtos da semana"],
        ["❌ Limpar lista semanal"],
        ["↩️ Voltar"]
    ], resize_keyboard=True)

# === HELPERS ===
def is_float(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except:
        return False

# === HANDLERS – fluxo principal de preços ===
def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.reply_text(
        "🛒 *Bot de Compras Inteligente*\n\n"
        "Escolha uma opção ou digite o nome do produto:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.reply_text(
        "❌ Operação cancelada.",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

def ask_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.reply_text(
        "Digite o nome do produto:",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_PRODUCT_NAME

def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "❌ cancelar":
        return cancel(update, context)

    nome = text.title()
    context.user_data["current_product"] = nome
    ws = get_sheet(ABA_PRODUTOS)
    historico = [r for r in ws.get_all_values()[1:] if r[0] == nome]

    if historico:
        preco_antigo = float(historico[-1][3] or 0)
        update.message.reply_text(
            f"📊 Último preço de *{nome}*: R${preco_antigo:.2f}\n\n"
            "Deseja atualizar o preço?",
            parse_mode="Markdown",
            reply_markup=yes_no_keyboard()
        )
        return AWAIT_CONFIRM_UPDATE

    formatos = (
        "📝 Formatos:\n"
        "• Frios: `0.5 kg 25.00`\n"
        "• Papel Higiênico: `4 rolos 40m 12.50`\n"
        "• Outros: `200g 2.69`, `1 caixa 3.99`"
    )
    update.message.reply_text(
        f"📦 Novo produto: *{nome}*\n\n{formatos}",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_DETAILS

def confirm_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    escolha = update.message.text.strip().lower()
    if escolha == "sim":
        update.message.reply_text(
            "Digite o novo preço (ex: 8.50):",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_PRICE
    if escolha == "não":
        return cancel(update, context)
    # se não for sim/não, trata como novo produto
    return handle_product_name(update, context)

def handle_details_and_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    if texto.lower() == "❌ cancelar":
        return cancel(update, context)

    partes = texto.replace(",", ".").split()
    if len(partes) >= 2 and is_float(partes[-1]):
        context.user_data["detalhes"] = " ".join(partes[:-1])
        return handle_price(update, context)

    context.user_data["detalhes"] = texto
    update.message.reply_text(
        "Digite o preço do produto (ex: 12.30):",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_PRICE

def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.replace(",", ".").strip()
    if texto.lower() == "❌ cancelar":
        return cancel(update, context)
    if not is_float(texto):
        update.message.reply_text(
            "⚠️ Preço inválido. Exemplo: 8.50",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_PRICE

    produto  = context.user_data["current_product"]
    detalhes = context.user_data.get("detalhes", "")
    preco    = float(texto)
    ws       = get_sheet(ABA_PRODUTOS)
    historico = [r for r in ws.get_all_values()[1:] if r[0] == produto]

    comparacao = ""
    if historico:
        preco_antigo = float(historico[-1][3] or 0)
        comparacao = (
            "🟢 Mais barato que R${:.2f}".format(preco_antigo)
            if preco < preco_antigo else
            "🔴 Mais caro que R${:.2f}".format(preco_antigo)
            if preco > preco_antigo else
            "🟡 Mesmo preço de R${:.2f}".format(preco_antigo)
        )

    resumo = f"R${preco:.2f}"
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    ws.append_row([produto, detalhes, preco, resumo, timestamp])

    update.message.reply_text(
        f"✅ Produto *{produto}* salvo!\n{resumo}\n{comparacao}",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.reply_text(
        "ℹ️ *Ajuda do Bot*\n\n"
        "• Digite o nome do produto ou use o menu\n"
        "• Formatos: `200g 2.69`, `0.5 kg 25.00`, etc\n"
        "• O bot compara com o último preço",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

# === HANDLERS – Compras Semanais ===
def compras_semanais_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.reply_text(
        "🛒 *Compras da Semana*\n\nEscolha uma opção:",
        parse_mode="Markdown",
        reply_markup=weekly_menu_keyboard()
    )
    return AWAIT_WEEKLY_MENU

def weekly_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "📥 Adicionar produtos":
        update.message.reply_text(
            "Digite o nome do produto para a lista semanal:",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_WEEKLY_ADD

    if text == "📋 Listar produtos da semana":
        ws = get_sheet(ABA_SEMANA)
        items = ws.get_all_values()[1:]
        if not items:
            update.message.reply_text(
                "📭 Lista semanal está vazia.",
                reply_markup=weekly_menu_keyboard()
            )
        else:
            lista = "\n".join(f"• {r[0]}" for r in items)
            update.message.reply_text(
                f"📋 *Lista da Semana:*\n{lista}",
                parse_mode="Markdown",
                reply_markup=weekly_menu_keyboard()
            )
        return AWAIT_WEEKLY_MENU

    if text == "❌ Limpar lista semanal":
        update.message.reply_text(
            "Tem certeza que deseja limpar a lista semanal?",
            reply_markup=yes_no_keyboard()
        )
        return AWAIT_CONFIRM_CLEAR_WEEKLY

    if text == "↩️ Voltar":
        return cancel(update, context)

    return compras_semanais_menu(update, context)

def weekly_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "❌ cancelar":
        return compras_semanais_menu(update, context)

    ws = get_sheet(ABA_SEMANA)
    ws.append_row([text, datetime.now().strftime("%d/%m/%Y %H:%M:%S")])
    update.message.reply_text(
        f"✅ *{text}* adicionado à lista semanal.",
        parse_mode="Markdown",
        reply_markup=weekly_menu_keyboard()
    )
    return AWAIT_WEEKLY_MENU

def weekly_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    escolha = update.message.text.strip().lower()
    if escolha == "sim":
        ws = get_sheet(ABA_SEMANA)
        header = ws.row_values(1)
        ws.clear()
        ws.append_row(header)
        update.message.reply_text(
            "🗑️ Lista semanal limpa.",
            reply_markup=weekly_menu_keyboard()
        )
        return AWAIT_WEEKLY_MENU
    if escolha == "não":
        return compras_semanais_menu(update, context)

    update.message.reply_text(
        "Responda apenas Sim ou Não.",
        reply_markup=yes_no_keyboard()
    )
    return AWAIT_CONFIRM_CLEAR_WEEKLY

# === CONVERSATION HANDLER ===
conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MAIN_MENU: [
            MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_name),
            MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command),
            MessageHandler(filters.Regex("^🛒 Compras Semanais$"), compras_semanais_menu),
        ],
        AWAIT_PRODUCT_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)],
        AWAIT_CONFIRM_UPDATE:  [
            MessageHandler(filters.Regex("^(Sim|Não)$"), confirm_update),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
        ],
        AWAIT_DETAILS:         [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_details_and_price)],
        AWAIT_PRICE:           [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_price)],
        AWAIT_WEEKLY_MENU:     [MessageHandler(filters.TEXT & ~filters.COMMAND, weekly_menu_handler)],
        AWAIT_WEEKLY_ADD:      [MessageHandler(filters.TEXT & ~filters.COMMAND, weekly_add_product)],
        AWAIT_CONFIRM_CLEAR_WEEKLY: [
            MessageHandler(filters.Regex("^(Sim|Não)$"), weekly_clear_confirm),
            MessageHandler(filters.TEXT & ~filters.COMMAND, weekly_menu_handler)
        ],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    allow_reentry=True
)

# === INICIALIZAÇÃO DO BOT & FLASK ===
application = Application.builder().token(TOKEN).build()
application.add_handler(conv)

app = Flask(__name__)
loop = asyncio.get_event_loop()

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data   = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    # dispara o processamento async
    loop.create_task(application.process_update(update))
    return "OK"

@app.route("/healthz", methods=["GET"])
def healthz():
    return "OK"

if __name__ == "__main__":
    # registra o webhook no Telegram
    application.bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")
    # inicia Flask
    app.run(host="0.0.0.0", port=PORT)
