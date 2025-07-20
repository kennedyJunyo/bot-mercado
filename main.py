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
(
    MAIN_MENU,
    AWAIT_PRODUCT_NAME,
    AWAIT_DETAILS,
    AWAIT_PRICE,
    AWAIT_UPDATE_PRICE,
    AWAIT_SHOPPING_LIST,
    CONFIRM_CLEAR_SHOPPING_LIST,
    AWAIT_DELETION,
    CONFIRM_DELETION
) = range(9)

SHOPPING_LIST_COL = 7  # Coluna G (índice 1-based para gspread)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_NOME)

def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("❌ Excluir Produto")],
        [KeyboardButton("📋 Listar Produtos"), KeyboardButton("🕒 Histórico")],
        [KeyboardButton("Compras da semana"), KeyboardButton("✅ Ver Lista Compras da semana")],
        [KeyboardButton("🗑️ Limpar Compras da Semana"), KeyboardButton("ℹ️ Ajuda")]
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

# Handler para o botão Adicionar Produto
async def ask_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Digite o nome do produto que deseja adicionar:", reply_markup=cancel_keyboard())
    return AWAIT_PRODUCT_NAME

# Handler para digitar o nome do produto (menu ou direto)
async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() in ["❌ cancelar", "cancelar", "sair", "parar"]:
        return await cancel(update, context)
    text = update.message.text.strip().title()
    context.user_data['current_product'] = text

    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]  # Ignora cabeçalho
    historico = [row for row in rows if row[0].lower() == text.lower()]

    if historico:
        ultimo = historico[-1]
        preco_ultimo = float(ultimo[3]) if ultimo[3] else None
        await update.message.reply_text(
            f"📊 '{text}' já cadastrado. Último preço: R${preco_ultimo:.2f}.\n\nDeseja atualizar o preço? (Sim/Não)",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("Sim"), KeyboardButton("Não")],
                [KeyboardButton("❌ Cancelar")]
            ], resize_keyboard=True)
        )
        return AWAIT_UPDATE_PRICE
    else:
        return await ask_details(update, context, text)

async def ask_details(update: Update, context: ContextTypes.DEFAULT_TYPE, produto=None):
    if not produto:
        produto = context.user_data.get('current_product', '')
    format_message = (
        "📝 Formatos de entrada:\n"
        "• Frios: `0.5 kg 25.00`\n"
        "• Papel Higiênico: `4 rolos 40m 12.50`\n"
        "• Outros: `200g 2.69`, `1 caixa 3.99`\n"
        "Você pode informar quantidade/unidade e preço juntos, ou só os detalhes."
    )
    await update.message.reply_text(
        f"📦 Novo produto: {produto}\n\n{format_message}",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_DETAILS

async def handle_update_price_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text == "sim":
        await update.message.reply_text("Digite os novos detalhes e preço (ex: `2 kg 12.00` ou apenas novo preço):", reply_markup=cancel_keyboard())
        return AWAIT_DETAILS
    elif text in ["não", "nao"]:
        await update.message.reply_text("Ok! Nenhuma alteração feita.", reply_markup=main_menu_keyboard())
        return MAIN_MENU
    elif text in ["❌ cancelar", "cancelar", "sair", "parar"]:
        return await cancel(update, context)
    else:
        await update.message.reply_text("Responda com 'Sim' ou 'Não'.", reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("Sim"), KeyboardButton("Não")],
            [KeyboardButton("❌ Cancelar")]
        ], resize_keyboard=True))
        return AWAIT_UPDATE_PRICE

async def handle_details_and_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() in ["❌ cancelar", "cancelar", "sair", "parar"]:
        return await cancel(update, context)
    produto = context.user_data["current_product"]
    detalhes_raw = update.message.text.strip()
    detalhes_split = detalhes_raw.replace(",", ".").split()
    preco = None

    try:
        if len(detalhes_split) >= 2 and is_float(detalhes_split[-1]):
            preco = float(detalhes_split[-1])
            detalhes = " ".join(detalhes_split[:-1])
        else:
            detalhes = detalhes_raw
    except Exception:
        detalhes = detalhes_raw

    if preco is not None:
        return await save_product_final(update, context, produto, detalhes, preco)
    else:
        context.user_data["detalhes"] = detalhes
        await update.message.reply_text("Digite o preço do produto (ex: 8.50):", reply_markup=cancel_keyboard())
        return AWAIT_PRICE

def is_float(text):
    try:
        float(text)
        return True
    except Exception:
        return False

async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() in ["❌ cancelar", "cancelar", "sair", "parar"]:
        return await cancel(update, context)
    produto = context.user_data["current_product"]
    detalhes = context.user_data.get("detalhes", "")
    preco_str = update.message.text.replace(",", ".")
    try:
        preco = float(preco_str)
    except ValueError:
        await update.message.reply_text("⚠️ Preço inválido, tente novamente. Exemplo: 8.50", reply_markup=cancel_keyboard())
        return AWAIT_PRICE
    return await save_product_final(update, context, produto, detalhes, preco)

async def save_product_final(update, context, produto, detalhes, preco):
    detalhes_split = detalhes.split()
    categoria = "Outros"
    unidade = ""
    resumo = ""
    calculo = ""
    try:
        if "Papel Higiênico" in produto:
            rolos = float(detalhes_split[0])
            metros = None
            for part in detalhes_split[1:]:
                if "m" in part:
                    metros = float(part.replace("m", ""))
                elif is_float(part):
                    metros = float(part)
            categoria = "Limpeza"
            unidade = "metros"
            preco_por_metro = preco / metros if metros else 0
            preco_por_rolo = preco / rolos if rolos else 0
            resumo = f"{rolos} rolos / {metros}m - R${preco:.2f} (R${preco_por_metro:.2f}/m, R${preco_por_rolo:.2f}/rolo)"
            calculo = f"Preço por metro: R${preco_por_metro:.2f}\nPreço por rolo: R${preco_por_rolo:.2f}"
        elif any(p in produto for p in ["Queijo", "Presunto", "Mussarela", "Peito De Peru"]):
            peso = float(detalhes_split[0].replace("kg", "")) if "kg" in detalhes_split[0] else float(detalhes_split[0])
            categoria = "Frios"
            unidade = "kg"
            preco_por_kg = preco / peso if peso else 0
            resumo = f"{peso}kg - R${preco:.2f} (R${preco_por_kg:.2f}/kg)"
            calculo = f"Preço por kg: R${preco_por_kg:.2f}"
        else:
            quantidade = None
            unidade = ""
            if len(detalhes_split) == 2:
                quantidade = float(detalhes_split[0])
                unidade = detalhes_split[1]
            elif detalhes_split:
                num = ''.join(filter(str.isdigit, detalhes_split[0]))
                quantidade = float(num) if num else 1.0
                unidade = ''.join(filter(str.isalpha, detalhes_split[0]))
                if not unidade and len(detalhes_split) > 1:
                    unidade = detalhes_split[1]
            resumo = f"{quantidade} {unidade} - R${preco:.2f}"
            calculo = ""
    except Exception as e:
        logging.error(f"Erro ao processar detalhes: {e}")
        await update.message.reply_text(
            "⚠️ Formato de detalhes inválido. Exemplos:\n"
            "• Frios: `0.5 kg 25.00`\n"
            "• Papel Higiênico: `4 rolos 40m 12.50`\n"
            "• Outros: `200g 2.69`, `1 caixa 3.99`\n"
            "Tente novamente.", reply_markup=cancel_keyboard()
        )
        return AWAIT_DETAILS

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

# ====== SHOPPING LIST HANDLERS =======

async def ask_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Digite os nomes dos produtos para sua lista de compras da semana, um por linha:\n\nExemplo:\nUva\nAbacate\nArroz\nFeijão",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_SHOPPING_LIST

async def handle_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() in ["❌ cancelar", "cancelar", "sair", "parar"]:
        return await cancel(update, context)
    produtos = [p.strip().title() for p in update.message.text.splitlines() if p.strip()]
    sheet = get_sheet()
    existing = sheet.get_all_values()
    start_row = 2  # 1-based index, row 2 is first data
    for i, produto in enumerate(produtos):
        sheet.update_cell(start_row + i, SHOPPING_LIST_COL, produto)
    await update.message.reply_text(
        "Lista de compras da semana salva! Use o botão '✅ Ver Lista Compras da semana' para visualizar.",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

async def show_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sheet = get_sheet()
    values = sheet.col_values(SHOPPING_LIST_COL)[1:]  # ignora cabeçalho
    lista = [v for v in values if v.strip()]
    if not lista:
        await update.message.reply_text("Nenhum produto salvo na lista de compras da semana.", reply_markup=main_menu_keyboard())
        return MAIN_MENU
    msg = "🛒 *Lista de Compras da Semana*\n\n"
    for item in lista:
        msg += f"• {item}\n"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup([
        [KeyboardButton("🗑️ Limpar Compras da Semana")],
        [KeyboardButton("❌ Cancelar")]
    ], resize_keyboard=True))
    return MAIN_MENU

async def confirm_clear_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Tem certeza que deseja apagar toda a lista de compras da semana? (Sim/Não)",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("Sim"), KeyboardButton("Não")],
            [KeyboardButton("❌ Cancelar")]
        ], resize_keyboard=True)
    )
    return CONFIRM_CLEAR_SHOPPING_LIST

async def handle_clear_shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text == "sim":
        sheet = get_sheet()
        values = sheet.col_values(SHOPPING_LIST_COL)
        for i in range(2, len(values) + 1):
            sheet.update_cell(i, SHOPPING_LIST_COL, "")
        await update.message.reply_text("Lista de compras da semana apagada!", reply_markup=main_menu_keyboard())
        return MAIN_MENU
    elif text == "não":
        await update.message.reply_text("Ok! Lista de compras da semana mantida.", reply_markup=main_menu_keyboard())
        return MAIN_MENU
    elif text in ["❌ cancelar", "cancelar", "sair", "parar"]:
        return await cancel(update, context)
    else:
        await update.message.reply_text("Responda com 'Sim' ou 'Não'.", reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("Sim"), KeyboardButton("Não")],
            [KeyboardButton("❌ Cancelar")]
        ], resize_keyboard=True))
        return CONFIRM_CLEAR_SHOPPING_LIST

# ====== RESTANTE DOS HANDLERS IGUAL (delete_product, confirm_deletion, execute_deletion, list_products, show_history, help_command...) =====

# (Aqui entram os handlers já existentes, sem alteração, ou copie e cole de sua versão anterior)

def build_conv_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_name),
            MessageHandler(filters.Regex("^Compras da semana$"), ask_shopping_list),
            MessageHandler(filters.Regex("^✅ Ver Lista Compras da semana$"), show_shopping_list),
            MessageHandler(filters.Regex("^🗑️ Limpar Compras da Semana$"), confirm_clear_shopping_list),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_name),
                MessageHandler(filters.Regex("^❌ Excluir Produto$"), delete_product),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^🕒 Histórico$"), show_history),
                MessageHandler(filters.Regex("^Compras da semana$"), ask_shopping_list),
                MessageHandler(filters.Regex("^✅ Ver Lista Compras da semana$"), show_shopping_list),
                MessageHandler(filters.Regex("^🗑️ Limpar Compras da Semana$"), confirm_clear_shopping_list),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
            ],
            AWAIT_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)],
            AWAIT_UPDATE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_update_price_decision)],
            AWAIT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_details_and_price)],
            AWAIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_price)],
            AWAIT_SHOPPING_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_shopping_list)],
            CONFIRM_CLEAR_SHOPPING_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_clear_shopping_list)],
            # ... demais estados (delete, confirm, etc)
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancelar$"), cancel)],
    )

# O resto do código Flask + Webhook permanece igual ao seu anterior.
# Certifique-se de incluir todos os handlers auxiliares (excluir produto, histórico, ajuda etc.) normalmente!
