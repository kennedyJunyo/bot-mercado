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
ABA_NOME = "Página1"  # Aba principal de produtos
ABA_SEMANA = "ComprasSemana"  # Aba para produtos da semana
CRED_FILE = "/etc/secrets/credentials.json"  # Ajuste conforme seu path

# === ESTADOS DO CONVERSATIONHANDLER ===
MAIN_MENU, AWAIT_PRODUCT_NAME, AWAIT_UPDATE_CONFIRMATION, AWAIT_DETAILS, AWAIT_PRICE, AWAIT_DELETION, CONFIRM_DELETION, AWAIT_WEEKLY_PRODUCTS, AWAIT_CONFIRM_CLEAR_WEEKLY = range(9)

# === LOGGING ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# === GOOGLE SHEETS ===
def get_sheet(sheet_name=ABA_NOME):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)

# === TECLADOS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("❌ Excluir Produto")],
        [KeyboardButton("📋 Listar Produtos"), KeyboardButton("🕒 Histórico")],
        [KeyboardButton("🛒 Compras Semanais"), KeyboardButton("ℹ️ Ajuda")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Cancelar")]],
        resize_keyboard=True
    )

def weekly_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📥 Adicionar produtos")],
        [KeyboardButton("📋 Listar produtos da semana")],
        [KeyboardButton("❌ Limpar lista semanal")],
        [KeyboardButton("🔙 Voltar")]
    ], resize_keyboard=True)

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

# --- Fluxo padrão de cadastro de produtos ---

async def ask_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Digite o nome do produto que deseja adicionar:", reply_markup=cancel_keyboard())
    return AWAIT_PRODUCT_NAME

async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    text = update.message.text.strip().title()
    context.user_data['current_product'] = text

    sheet = get_sheet()
    rows = sheet.get_all_values()[1:]  # Ignora cabeçalho
    historico = [row for row in rows if row[0] == text]

    if historico:
        ultimo = historico[-1]
        preco_ultimo = float(ultimo[3]) if ultimo[3] else None
        await update.message.reply_text(
            f"📊 Produto *{text}* já cadastrado com preço: R${preco_ultimo:.2f}.\n"
            "Deseja atualizar o preço deste produto? (Digite SIM ou NÃO)",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("SIM")], [KeyboardButton("NÃO")], [KeyboardButton("❌ Cancelar")]],
                resize_keyboard=True
            )
        )
        return AWAIT_UPDATE_CONFIRMATION
    else:
        format_message = (
            "📝 Formatos de entrada:\n"
            "• Frios: `0.5 kg 25.00`\n"
            "• Papel Higiênico: `4 rolos 40m 12.50`\n"
            "• Outros: `200g 2.69`, `1 caixa 3.99`\n"
            "Você pode informar quantidade/unidade e preço juntos, ou só os detalhes."
        )
        await update.message.reply_text(
            f"📦 Novo produto: {text}\n\n{format_message}",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_DETAILS

async def handle_update_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text in ["NÃO", "NAO", "❌ NÃO"]:
        await update.message.reply_text("❌ Atualização cancelada.", reply_markup=main_menu_keyboard())
        return MAIN_MENU
    elif text in ["SIM", "✅ SIM"]:
        await update.message.reply_text("Digite os detalhes e preço do produto:", reply_markup=cancel_keyboard())
        return AWAIT_DETAILS
    else:
        await update.message.reply_text("Por favor, responda apenas com SIM ou NÃO.", reply_markup=cancel_keyboard())
        return AWAIT_UPDATE_CONFIRMATION

async def handle_details_and_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
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
    if update.message.text == "❌ Cancelar":
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

# --- Exclusão, listagem, histórico e ajuda mantidos igual ao código antigo ---

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
            idx_to_delete = idx + 1
    if idx_to_delete:
        context.user_data['product_to_delete'] = produto
        await update.message.reply_text(
            f"⚠️ Confirmar exclusão de *{produto}*\nDigite 'SIM' para confirmar ou 'NÃO' para cancelar",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("✅ SIM"), KeyboardButton("❌ NÃO")]], resize_keyboard=True
            )
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
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛒 *Ajuda do Bot de Compras*\n\n"
        "🔹 *Como usar:*\n"
        "• Digite o nome do produto direto ou use o menu\n"
        "• Forneça detalhes e preço juntos (ex: '200g 2.69', '0.5 kg 25.00') ou só os detalhes\n"
        "• O bot compara com o preço anterior\n"
        "• `➕ Adicionar Produto`: Cadastra novos itens\n"
        "• `❌ Excluir Produto`: Remove produtos cadastrados\n"
        "• `📋 Listar Produtos`: Mostra todos os itens\n"
        "• `🕒 Histórico`: Consulta histórico de preços\n"
        "• `🛒 Compras Semanais`: Gerencia lista temporária semanal\n"
        "\n"
        "📝 Formatos de detalhes:\n"
        "• Frios: `0.5 kg 25.00`\n"
        "• Papel Higiênico: `4 rolos 40m 12.50`\n"
        "• Outros: `200g 2.69`, `1 caixa 3.99`\n"
        "\n"
        "✉️ Dúvidas? Contate o suporte."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# --- NOVO: Fluxo Compras Semanais ---

async def weekly_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Menu Compras Semanais*\n\nEscolha uma opção:",
        reply_markup=weekly_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def ask_weekly_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✍️ *Envie a lista de produtos da semana,* um por linha:\n\n"
        "Exemplo:\nFeijão\nArroz\nBatata\nAbóbora\n\n"
        "Você pode enviar várias vezes para adicionar mais produtos.",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    return AWAIT_WEEKLY_PRODUCTS

async def save_weekly_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    texto = update.message.text.strip()
    produtos = [linha.strip().title() for linha in texto.split("\n") if linha.strip()]
    sheet = get_sheet(sheet_name=ABA_SEMANA)
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    for produto in produtos:
        sheet.append_row([produto, timestamp])
    await update.message.reply_text(
        f"✅ {len(produtos)} produtos adicionados à lista semanal.",
        reply_markup=weekly_menu_keyboard()
    )
    return MAIN_MENU

async def list_weekly_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sheet = get_sheet(sheet_name=ABA_SEMANA)
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        await update.message.reply_text(
            "📭 A lista semanal está vazia.",
            reply_markup=weekly_menu_keyboard()
        )
        return MAIN_MENU
    message = "🛒 *Lista de Compras da Semana*\n\n"
    for row in rows[1:]:
        message += f"🏷️ {row[0]}\n"
    await update.message.reply_text(message, reply_markup=weekly_menu_keyboard(), parse_mode="Markdown")
    return MAIN_MENU

async def ask_clear_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ *Tem certeza que deseja limpar toda a lista semanal?*\n"
        "Digite 'SIM' para confirmar ou 'NÃO' para cancelar.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("SIM")], [KeyboardButton("NÃO")], [KeyboardButton("❌ Cancelar")]],
            resize_keyboard=True
        )
    )
    return AWAIT_CONFIRM_CLEAR_WEEKLY

async def clear_weekly_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text == "SIM":
        sheet = get_sheet(sheet_name=ABA_SEMANA)
        rows = sheet.get_all_values()
        # Apaga todas as linhas menos o cabeçalho
        if len(rows) > 1:
            sheet.delete_rows(2, len(rows))
        await update.message.reply_text(
            "🗑️ Lista semanal limpa.",
            reply_markup=weekly_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Limpeza cancelada.",
            reply_markup=weekly_menu_keyboard()
        )
    return MAIN_MENU

# === AUX ===

def run_flask_app():
    app = Flask(__name__)

    @app.route(f"/{TOKEN}", methods=["POST"])
    def telegram_webhook():
        from telegram import Bot
        bot = Bot(token=TOKEN)
        update = Update.de_json(request.get_json(force=True), bot)
        asyncio.run(application.process_update(update))
        return "OK"

    app.run(port=5000)

# === APLICAÇÃO ===

application = Application.builder().token(TOKEN).build()

# Handlers fluxo principal
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MAIN_MENU: [
            MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_name),
            MessageHandler(filters.Regex("^❌ Excluir Produto$"), delete_product),
            MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
            MessageHandler(filters.Regex("^🕒 Histórico$"), show_history),
            MessageHandler(filters.Regex("^🛒 Compras Semanais$"), weekly_menu),
            MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command),
        ],
        AWAIT_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)],
        AWAIT_UPDATE_CONFIRMATION: [MessageHandler(filters.Regex("^(SIM|NÃO|NAO|✅ SIM|❌ NÃO)$"), handle_update_confirmation)],
        AWAIT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_details_and_price)],
        AWAIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_price)],
        AWAIT_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_deletion)],
        CONFIRM_DELETION: [MessageHandler(filters.Regex("^(SIM|NÃO|NAO|✅ SIM|❌ NÃO)$"), execute_deletion)],
        AWAIT_WEEKLY_PRODUCTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_weekly_products)],
        AWAIT_CONFIRM_CLEAR_WEEKLY: [MessageHandler(filters.Regex("^(SIM|NÃO|NAO|✅ SIM|❌ NÃO)$"), clear_weekly_products)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

# Handlers menu compras semanais
application.add_handler(conv_handler)

# Para o menu compras semanais dentro do MAIN_MENU, tratamos opções abaixo:
async def weekly_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📥 Adicionar produtos":
        return await ask_weekly_products(update, context)
    elif text == "📋 Listar produtos da semana":
        return await list_weekly_products(update, context)
    elif text == "❌ Limpar lista semanal":
        return await ask_clear_weekly(update, context)
    elif text == "🔙 Voltar":
        await update.message.reply_text(
            "Voltando ao menu principal.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    else:
        await update.message.reply_text(
            "Selecione uma opção válida.",
            reply_markup=weekly_menu_keyboard()
        )
        return MAIN_MENU

# Interceptar mensagens do menu compras semanais
application.add_handler(MessageHandler(filters.Regex("^(📥 Adicionar produtos|📋 Listar produtos da semana|❌ Limpar lista semanal|🔙 Voltar)$"), weekly_menu_handler))

# Rodar localmente (ou no seu ambiente)
if __name__ == "__main__":
    # run_flask_app()  # Use se quiser rodar com Flask + Webhook
    application.run_polling()
