import json
import logging
import os
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
TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- ESTADOS DA CONVERSA ---
MAIN_MENU, AWAIT_PRODUCT, AWAIT_DETAILS, AWAIT_DELETION, CONFIRM_DELETION = range(5)

# --- VERIFICA/CRIA O ARQUIVO dados.json ---
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

# --- CARREGA/SALVA DADOS ---
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

# --- TECLADOS ---
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

# --- HANDLERS PRINCIPAIS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Bot de Compras Inteligente* 🛒\n\nEscolha uma opção:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
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
        
        # Processamento dos dados (igual ao seu código original)
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
        
        # Salva e envia confirmação
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

# --- (MANTENHA O RESTO DO SEU CÓDIGO ORIGINAL, como delete_product, list_products, etc.) ---

def main():
    check_data_file()  # Garante que o arquivo existe
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
            AWAIT_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_details)
            ]
        },
        fallbacks=[CommandHandler("cancelar", cancel)]
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()
