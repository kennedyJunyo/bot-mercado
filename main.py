import json
import logging
import os
import threading
import asyncio
from datetime import datetime
from flask import Flask
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
TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
PORT = int(os.environ.get('PORT', 10000))
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- FLASK PARA O RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "🛒 Bot de Compras está rodando!", 200

@app.route('/healthz')
def health_check():
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- ESTADOS DA CONVERSA ---
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS, AWAIT_DELETION, CONFIRM_DELETION = range(5)

# --- BANCO DE DADOS ---
def check_data_file():
    try:
        if not os.path.exists("dados.json"):
            with open("dados.json", "w") as f:
                json.dump({"produtos": {}, "historico": {}}, f)
            logging.info("✅ Arquivo dados.json criado com sucesso!")
        else:
            logging.info("📄 Arquivo dados.json já existe")
    except Exception as e:
        logging.error(f"❌ Erro ao criar dados.json: {e}")

def load_data():
    try:
        with open("dados.json", "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"produtos": {}, "historico": {}}

def save_data(data):
    try:
        with open("dados.json", "w") as f:
            json.dump(data, f, indent=2)
        logging.info("💾 Dados salvos com sucesso!")
    except Exception as e:
        logging.error(f"❌ Falha ao salvar dados: {e}")

# --- TELCADOS ---
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

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Bot de Compras Inteligente* 🛒\n\nEscolha uma opção:",
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

async def handle_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    
    product = update.message.text.title()
    context.user_data['current_product'] = product
    data = load_data()
    
    format_message = (
        "📝 *Formato de entrada:*\n"
        "• Frios: `0.5 25.00` (0.5kg a R$25)\n"
        "• Papel Higiênico: `4 40 12.50` (4 rolos, 40m, R$12.50)\n"
        "• Outros: `2 litro 8.50` (2 litros a R$8.50)"
    )
    
    if product in data["produtos"]:
        await update.message.reply_text(
            f"📊 Último preço de {product}: R${data['produtos'][product]['preco']:.2f}\n\n{format_message}",
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
        
        if "Papel Higiênico" in product:
            rolos, metros, preco = float(details[0]), float(details[1]), float(details[2])
            data["produtos"][product] = {
                "categoria": "Limpeza",
                "rolos": rolos,
                "metros": metros,
                "preco": preco,
                "preco_por_metro": preco / metros,
                "preco_por_rolo": preco / rolos,
                "unidade": "metros",
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d")
            }
        elif any(p in product for p in ["Queijo", "Presunto", "Mussarela", "Peito de Peru"]):
            peso, preco = float(details[0]), float(details[1])
            data["produtos"][product] = {
                "categoria": "Frios",
                "peso": peso,
                "preco": preco,
                "preco_por_kg": preco / peso,
                "unidade": "kg",
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d")
            }
        else:
            quantidade, unidade, preco = float(details[0]), details[1], float(details[2])
            data["produtos"][product] = {
                "categoria": "Outros",
                "quantidade": quantidade,
                "unidade": unidade,
                "preco": preco,
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d")
            }
        
        # Atualiza histórico
        if product not in data["historico"]:
            data["historico"][product] = []
        data["historico"][product].append({
            "data": datetime.now().strftime("%Y-%m-%d"),
            "preco": data["produtos"][product]["preco"]
        })
        
        save_data(data)
        await update.message.reply_text(
            f"✅ *{product}* salvo com sucesso!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    except Exception as e:
        logging.error(f"Erro ao salvar produto: {e}")
        await update.message.reply_text(
            "⚠️ Formato inválido. Use:\n"
            "• Frios: `0.5 25.00`\n"
            "• Papel Higiênico: `4 40 12.50`\n"
            "• Outros: `2 litro 8.50`",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_DETAILS

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["produtos"]:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado para excluir.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    
    products = [[KeyboardButton(name)] for name in data["produtos"].keys()]
    products.append([KeyboardButton("❌ Cancelar")])
    
    await update.message.reply_text(
        "🗑️ Selecione o produto a excluir:",
        reply_markup=ReplyKeyboardMarkup(products, resize_keyboard=True)
    )
    return AWAIT_DELETION

async def confirm_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    
    product = update.message.text
    data = load_data()
    
    if product in data["produtos"]:
        context.user_data['product_to_delete'] = product
        await update.message.reply_text(
            f"⚠️ Confirmar exclusão de *{product}*?\n\n"
            f"Preço atual: R${data['produtos'][product]['preco']:.2f}\n"
            "Digite 'SIM' para confirmar ou 'NÃO' para cancelar",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✅ SIM"), KeyboardButton("❌ NÃO")]
            ], resize_keyboard=True)
        )
        return CONFIRM_DELETION
    else:
        await update.message.reply_text(
            f"ℹ️ Produto '{product}' não encontrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

async def execute_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.upper()
    if text == "✅ SIM":
        product = context.user_data['product_to_delete']
        data = load_data()
        data["produtos"].pop(product, None)
        data["historico"].pop(product, None)
        save_data(data)
        await update.message.reply_text(
            f"🗑️ *{product}* foi excluído permanentemente.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ Exclusão cancelada.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["produtos"]:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    
    message = "📋 *Lista de Produtos*\n\n"
    for product, details in data["produtos"].items():
        message += f"🏷️ *{product}*\n• Preço: R${details['preco']:.2f}\n\n"
    
    await update.message.reply_text(
        message,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
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
        await update.message.reply_text(
            f"ℹ️ Nenhum histórico para {product}",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
    
    history = data["historico"][product][-5:]
    message = f"📊 *Histórico de {product}*\n\n"
    for entry in history:
        message += f"📅 {entry['data']}: R${entry['preco']:.2f}\n"
    
    await update.message.reply_text(
        message,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛒 *Ajuda do Bot de Compras*\n\n"
        "🔹 *Como usar:*\n"
        "• `➕ Adicionar Produto`: Cadastra novos itens\n"
        "• `❌ Excluir Produto`: Remove produtos cadastrados\n"
        "• `📋 Listar Produtos`: Mostra todos os itens\n"
        "• `🕒 Histórico`: Consulta histórico de preços\n\n"
        "📝 *Formatos de entrada:*\n"
        "• Frios: `0.5 25.00` (0.5kg a R$25)\n"
        "• Papel Higiênico: `4 40 12.50` (4 rolos, 40m, R$12.50)\n"
        "• Outros: `2 litro 8.50` (2 litros a R$8.50)"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

# --- MAIN ---
async def main():
    check_data_file()
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), handle_product_name),
                MessageHandler(filters.Regex("^❌ Excluir Produto$"), delete_product),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^🕒 Histórico$"), show_history),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command)
            ],
            AWAIT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_details)],
            AWAIT_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_deletion)],
            CONFIRM_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_deletion)]
        },
        fallbacks=[CommandHandler("cancelar", cancel)]
    )
    
    application.add_handler(conv_handler)
    
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.start()
    
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
