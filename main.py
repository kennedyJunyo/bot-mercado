import json
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# --- CONFIGURAÇÕES ---
import os
TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- ESTADOS DA CONVERSA ---
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS, CONFIRM_ADD = range(4)

# --- BANCO DE DADOS ---
def load_data():
    try:
        with open("dados.json", "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"produtos": {}, "historico": {}}

def save_data(data):
    with open("dados.json", "w") as f:
        json.dump(data, f, indent=2)

# --- TELCADOS ---
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Adicionar Produto")],
        [KeyboardButton("Listar Produtos"), KeyboardButton("Histórico")],
        [KeyboardButton("Ajuda")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Cancelar")]],
        resize_keyboard=True
    )

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Bot de Compras Inteligente* 🛒\n\n"
        "Digite o nome de um produto (ex: 'Mussarela') ou use os botões:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = update.message.text.title()
    context.user_data['current_product'] = product
    data = load_data()

    if product in data["produtos"]:
        last_price = data["produtos"][product]["preco"]
        await update.message.reply_text(
            f"📊 Último preço de {product}: R${last_price:.2f}\n\n"
            "Digite os novos detalhes:\n"
            "• Frios: PESO PREÇO (ex: 0.5 25.00)\n"
            "• Papel Higiênico: ROLOS METROS PREÇO (ex: 4 40 12.50)\n"
            "• Outros: QUANTIDADE UNIDADE PREÇO (ex: 2 litro 8.50)",
            reply_markup=cancel_keyboard()
        )
    else:
        await update.message.reply_text(
            f"📦 Novo produto: {product}\n"
            "Digite os detalhes no formato acima:",
            reply_markup=cancel_keyboard()
        )
    return AWAIT_DETAILS

async def handle_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        product = context.user_data['current_product']
        details = update.message.text.split()
        data = load_data()

        # Processamento para Papel Higiênico
        if "Papel Higiênico" in product:
            rolos, metros, preco = float(details[0]), float(details[1]), float(details[2])
            preco_por_metro = preco / metros
            preco_por_rolo = preco / rolos

            data["produtos"][product] = {
                "categoria": "Limpeza",
                "rolos": rolos,
                "metros": metros,
                "preco": preco,
                "preco_por_metro": preco_por_metro,
                "preco_por_rolo": preco_por_rolo,
                "unidade": "metros",
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d")
            }

            msg = (
                f"🧻 *{product}*\n"
                f"• {rolos} rolos | {metros}m\n"
                f"• Preço total: R${preco:.2f}\n"
                f"• Preço por metro: R${preco_por_metro:.4f}\n"
                f"• Preço por rolo: R${preco_por_rolo:.2f}"
            )

        # Processamento para Frios
        elif any(p in product for p in ["Queijo", "Presunto", "Mussarela", "Peito de Peru"]):
            peso, preco = float(details[0]), float(details[1])
            preco_por_kg = preco / peso

            data["produtos"][product] = {
                "categoria": "Frios",
                "peso": peso,
                "preco": preco,
                "preco_por_kg": preco_por_kg,
                "unidade": "kg",
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d")
            }

            msg = (
                f"🧀 *{product}*\n"
                f"• Peso: {peso}kg\n"
                f"• Preço total: R${preco:.2f}\n"
                f"• Preço por kg: R${preco_por_kg:.2f}"
            )

        # Outros produtos
        else:
            quantidade, unidade, preco = float(details[0]), details[1], float(details[2])

            data["produtos"][product] = {
                "categoria": "Outros",
                "quantidade": quantidade,
                "unidade": unidade,
                "preco": preco,
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d")
            }

            msg = (
                f"📦 *{product}*\n"
                f"• Quantidade: {quantidade} {unidade}\n"
                f"• Preço total: R${preco:.2f}"
            )

        # Histórico de preços
        if "historico" not in data:
            data["historico"] = {}
        if product not in data["historico"]:
            data["historico"][product] = []

        data["historico"][product].append({
            "data": datetime.now().strftime("%Y-%m-%d"),
            "preco": preco
        })

        save_data(data)
        await update.message.reply_text(
            f"{msg}\n\n✅ *Dados salvos com sucesso!*",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    except Exception as e:
        await update.message.reply_text(
            f"⚠️ *Erro no formato:* {str(e)}\n"
            "Digite os dados corretamente ou /cancel",
            parse_mode="Markdown"
        )
        return AWAIT_DETAILS

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["produtos"]:
        await update.message.reply_text("📭 Nenhum produto cadastrado.")
        return MAIN_MENU

    message = "📋 *Lista de Produtos*\n\n"
    for product, details in data["produtos"].items():
        message += f"🏷️ *{product}*\n"
        message += f"• Preço: R${details['preco']:.2f}\n"
        message += f"• Última atualização: {details['ultima_atualizacao']}\n\n"

    await update.message.reply_text(
        message,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not context.args:
        await update.message.reply_text(
            "Digite: /historico NomeDoProduto\nEx: /historico Mussarela",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    product = ' '.join(context.args).title()
    if product not in data.get("historico", {}):
        await update.message.reply_text(
            f"ℹ️ Nenhum histórico para {product}",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    history = data["historico"][product][-5:]  # Últimos 5 registros
    message = f"📊 *Histórico de {product}*\n\n"
    for entry in history:
        message += f"📅 {entry['data']}: R${entry['preco']:.2f}\n"

    await update.message.reply_text(
        message,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Operação cancelada.",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Ajuda do Bot de Compras*\n\n"
        "• Digite o nome de um produto para cadastrar\n"
        "• Comandos:\n"
        "/listar - Lista todos produtos\n"
        "/historico Nome - Mostra histórico\n"
        "/ajuda - Mostra esta mensagem\n\n"
        "📝 Formatos de entrada:\n"
        "• Frios: 0.5 25.00 (0.5kg a R$25)\n"
        "• Papel Higiênico: 4 40 12.50 (4 rolos, 40m, R$12.50)",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    return MAIN_MENU

# --- MAIN ---
def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("ajuda", help),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^Adicionar Produto$"), handle_product_name),
                MessageHandler(filters.Regex("^Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^Histórico$"), show_history),
                MessageHandler(filters.Regex("^Ajuda$"), help)
            ],
            AWAIT_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_details),
                CommandHandler("cancelar", cancel)
            ]
        },
        fallbacks=[CommandHandler("cancelar", cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("listar", list_products))
    application.add_handler(CommandHandler("historico", show_history))

    application.run_polling()

from flask import Flask
from threading import Thread

# Cria um servidor web simples para evitar timeout
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de Compras está online! ✅"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Inicia o Flask em uma thread separada
Thread(target=run_flask).start()

if __name__ == "__main__":
    main()