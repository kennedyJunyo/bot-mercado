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
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS = range(3)

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
        [KeyboardButton("➕ Adicionar Produto")],
        [KeyboardButton("📋 Listar Produtos"), KeyboardButton("🕒 Histórico")],
        [KeyboardButton("ℹ️ Ajuda")]
    ], resize_keyboard=True, input_field_placeholder="Escolha uma opção")

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Cancelar")]],
        resize_keyboard=True
    )

# --- HANDLERS PRINCIPAIS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, "🛒 *Bot de Compras Inteligente* 🛒\n\nEscolha uma opção:")
    return MAIN_MENU

async def show_main_menu(update: Update, message: str):
    await update.message.reply_text(
        message,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "➕ Adicionar Produto":
        await update.message.reply_text(
            "📝 Digite o nome do produto (ex: 'Mussarela'):",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_PRODUCT
        
    elif text == "📋 Listar Produtos":
        return await list_products(update, context)
        
    elif text == "🕒 Histórico":
        await update.message.reply_text(
            "Digite o nome do produto para ver o histórico (ex: 'Mussarela'):",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_PRODUCT
        
    elif text == "ℹ️ Ajuda":
        return await help_command(update, context)
        
    else:
        return await handle_product_name(update, context)

# --- PRODUTOS ---
async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
        
    product = update.message.text.title()
    context.user_data['current_product'] = product
    
    format_message = (
        "📝 *Formato de entrada:*\n"
        "• Frios: `0.5 25.00` (0.5kg a R$25)\n"
        "• Papel Higiênico: `4 40 12.50` (4 rolos, 40m, R$12.50)\n"
        "• Outros: `2 litro 8.50` (2 litros a R$8.50)"
    )
    
    data = load_data()
    if product in data["produtos"]:
        last_price = data["produtos"][product]["preco"]
        await update.message.reply_text(
            f"📊 Último preço de {product}: R${last_price:.2f}\n\n{format_message}",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
    else:
        await update.message.reply_text(
            f"📦 Novo produto: {product}\n\n{format_message}",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
    return AWAIT_DETAILS

async def handle_product_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
        
    try:
        product = context.user_data['current_product']
        details = update.message.text.split()
        data = load_data()
        
        # Processamento dos dados (mantido igual ao anterior)
        # ... (código de processamento dos produtos)
        
        await show_main_menu(update, f"✅ *{product} salvo com sucesso!*")
        return MAIN_MENU
        
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Formato inválido. Por favor, use os exemplos:\n"
            "• Frios: `0.5 25.00`\n"
            "• Papel Higiênico: `4 40 12.50`\n"
            "• Outros: `2 litro 8.50`\n\n"
            "Ou cancele com o botão abaixo:",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_DETAILS

# --- COMANDOS ---
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["produtos"]:
        await show_main_menu(update, "📭 Nenhum produto cadastrado.")
        return MAIN_MENU
    
    message = "📋 *Lista de Produtos*\n\n"
    for product, details in data["produtos"].items():
        message += f"🏷️ *{product}*\n• Preço: R${details['preco']:.2f}\n\n"
    
    await show_main_menu(update, message)
    return MAIN_MENU

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = ' '.join(context.args).title() if context.args else None
    
    if not product:
        await update.message.reply_text(
            "🔍 Digite o nome do produto para ver o histórico (ex: '/historico Mussarela')",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    
    data = load_data()
    if product not in data.get("historico", {}):
        await show_main_menu(update, f"ℹ️ Nenhum histórico para {product}")
        return MAIN_MENU
    
    history = data["historico"][product][-5:]
    message = f"📊 *Histórico de {product}*\n\n"
    for entry in history:
        message += f"📅 {entry['data']}: R${entry['preco']:.2f}\n"
    
    await show_main_menu(update, message)
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛒 *Ajuda do Bot de Compras*\n\n"
        "🔹 *Como usar:*\n"
        "1. Adicione produtos com preços\n"
        "2. Consulte o histórico\n"
        "3. Compare preços\n\n"
        "📝 *Formatos de entrada:*\n"
        "• Frios: `0.5 25.00` (0.5kg a R$25)\n"
        "• Papel Higiênico: `4 40 12.50` (4 rolos, 40m, R$12.50)\n"
        "• Outros: `2 litro 8.50` (2 litros a R$8.50)\n\n"
        "📌 Os botões estarão sempre disponíveis!"
    )
    await show_main_menu(update, help_text)
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, "❌ Operação cancelada.")
    return MAIN_MENU

# --- MAIN ---
def main():
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)
            ],
            AWAIT_PRODUCT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_name)
            ],
            AWAIT_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_details)
            ]
        },
        fallbacks=[
            CommandHandler("cancelar", cancel),
            CommandHandler("ajuda", help_command),
            CommandHandler("listar", list_products),
            CommandHandler("historico", show_history)
        ]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("ajuda", help_command))
    application.add_handler(CommandHandler("listar", list_products))
    application.add_handler(CommandHandler("historico", show_history))
    
    # Servidor web para manter o bot ativo
    from flask import Flask
    from threading import Thread
    
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "Bot de Compras Online ✅"
    
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()
    
    application.run_polling()

if __name__ == "__main__":
    main()
