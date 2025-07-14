import json
import logging
import os
import threading
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
PORT = int(os.environ.get('PORT', 10000))  # Porta para o Render
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

# --- (MANTENHA TODAS AS SUAS FUNÇÕES ORIGINAIS AQUI) ---
# [Cole todas as outras funções do seu código ANTIGO aqui]
# - load_data()
# - save_data()
# - main_menu_keyboard()
# - cancel_keyboard()
# - start()
# - cancel()
# - handle_product_name()
# - handle_product_details() 
# - delete_product()
# - confirm_deletion()
# - execute_deletion()
# - list_products()
# - show_history()
# - help_command()

# --- MAIN AJUSTADO ---
async def main():
    check_data_file()
    application = Application.builder().token(TOKEN).build()
    
    # Configuração do ConversationHandler (igual ao seu código)
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
    
    # Reseta conexões anteriores e inicia o bot
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.start()
    
    # Inicia o Flask em segundo plano
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
