import asyncio
import logging
import os
import re
from threading import Thread
import uuid
from typing import Optional  # Adicionado para melhor tipagem, se desejar

# Flask
from flask import Flask, request

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)
from supabase import create_client, Client

# ========================
# Configuração Flask
# ========================
app = Flask(__name__)

@app.route("/healthz")
def health_check():
    return "OK", 200

@app.route("/")
def home():
    return "🛒 Bot de Compras está no ar!", 200

# ========================
# Configurações Bot / Supabase
# ========================
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
WEBHOOK_DOMAIN = os.environ.get("WEBHOOK_DOMAIN")  # Ex: https://bot-mercado.onrender.com

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL e SUPABASE_KEY devem ser definidos nas variáveis de ambiente.")
if not WEBHOOK_DOMAIN:
    raise ValueError("WEBHOOK_DOMAIN deve ser definido (ex: https://bot-mercado.onrender.com)")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ========================
# Estados ConversationHandler (ajuste conforme seu código)
# ========================
(
    MAIN_MENU,
    AWAIT_PRODUCT_DATA,
    CONFIRM_PRODUCT,
    AWAIT_EDIT_DELETE_CHOICE,
    AWAIT_EDIT_PRICE,
    AWAIT_DELETION_CHOICE,
    CONFIRM_DELETION,
    SEARCH_PRODUCT_INPUT,
    AWAIT_ENTRY_CHOICE,
    AWAIT_INVITE_CODE,
    AWAIT_INVITE_CODE_INPUT
) = range(11)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# ========================
# Variáveis Globais para o Loop e Application
# ========================
bot_application = None
bot_event_loop = None

# ========================
# Funções Auxiliares
# ========================
def format_price(price):
    try:
        price_float = float(price)
    except (ValueError, TypeError):
        return "0,00"
    return "{:,.2f}".format(price_float).replace(".", ",")

def parse_price(price_str):
    try:
        return float(price_str.replace(',', '.'))
    except ValueError:
        return None

def calculate_unit_price(unit_str, price):
    unit_str_lower = unit_str.lower().strip()
    try:
        price = float(price)
    except (ValueError, TypeError):
        return {'preco_unitario': price, 'unidade': unit_str}

    patterns = {
        'rolos_e_metros': r'(\d+(?:[.]?\d*))\s*rolos?\s+(\d+(?:[.]?\d*))\s*m',
        'multiplas_embalagens': r'(\d+(?:[.]?\d*))\s*(tubos?|pacotes?|caixas?)\s*de\s*(\d+(?:[.]?\d*))\s*(kg|g|l|ml)',
        'kg': r'(\d+(?:[.]?\d*))\s*kg',
        'g': r'(\d+(?:[.]?\d*))\s*g',
        'l': r'(\d+(?:[.]?\d*))\s*l',
        'ml': r'(\d+(?:[.]?\d*))\s*ml',
        'und': r'(\d+(?:[.]?\d*))\s*und',
        'rolo_simples': r'(\d+(?:[.]?\d*))\s*rolos?',
        'folhas': r'(\d+(?:[.]?\d*))\s*folhas?',
    }

    if re.search(patterns['rolos_e_metros'], unit_str_lower):
        match = re.search(patterns['rolos_e_metros'], unit_str_lower)
        rolos = float(match.group(1))
        metros = float(match.group(2))
        return {'preco_por_rolo': price / rolos, 'preco_por_metro': price / metros,
                'unidade': f"{rolos} rolos, {metros}m"} if rolos > 0 and metros > 0 else {}

    elif re.search(patterns['multiplas_embalagens'], unit_str_lower):
        match = re.search(patterns['multiplas_embalagens'], unit_str_lower)
        qtd_emb = float(match.group(1))
        tipo_emb = match.group(2)
        tam_uni = float(match.group(3))
        uni_med = match.group(4).lower()
        if qtd_emb > 0:
            total = qtd_emb * tam_uni
            preco_emb = price / qtd_emb
            if uni_med in ['g', 'ml']:
                return {'preco_por_embalagem': preco_emb,
                        'preco_por_100': price / total * 100,
                        'unidade': f"{qtd_emb} {tipo_emb} de {tam_uni}{uni_med}"}
            elif uni_med in ['kg', 'l']:
                base = price / total
                return {'preco_por_embalagem': preco_emb,
                        'preco_por_unidade_base': base,
                        'preco_por_100_base': base * 100,
                        'unidade': f"{qtd_emb} {tipo_emb} de {tam_uni}{uni_med}"}
            else:
                return {'preco_por_embalagem': preco_emb,
                        'unidade': f"{qtd_emb} {tipo_emb} de {tam_uni}{uni_med}"}

    elif re.search(patterns['kg'], unit_str_lower):
        kg = float(re.search(patterns['kg'], unit_str_lower).group(1))
        return {'preco_por_kg': price / kg, 'unidade': f"{kg}kg"} if kg > 0 else {}

    elif re.search(patterns['g'], unit_str_lower):
        g = float(re.search(patterns['g'], unit_str_lower).group(1))
        return {'preco_por_100g': price / g * 100, 'unidade': f"{g}g"} if g > 0 else {}

    elif re.search(patterns['l'], unit_str_lower):
        l = float(re.search(patterns['l'], unit_str_lower).group(1))
        total_ml = l * 1000
        return {'preco_por_litro': price / l,
                'preco_por_100ml': price / total_ml * 100 if total_ml > 0 else 0,
                'unidade': f"{l}L"} if l > 0 else {}

    elif re.search(patterns['ml'], unit_str_lower):
        ml = float(re.search(patterns['ml'], unit_str_lower).group(1))
        return {'preco_por_100ml': price / ml * 100, 'unidade': f"{ml}ml"} if ml > 0 else {}

    elif re.search(patterns['und'], unit_str_lower):
        und = float(re.search(patterns['und'], unit_str_lower).group(1))
        return {'preco_por_unidade': price / und, 'unidade': f"{und} und"} if und > 0 else {}

    elif re.search(patterns['rolo_simples'], unit_str_lower):
        rolos = float(re.search(patterns['rolo_simples'], unit_str_lower).group(1))
        return {'preco_por_rolo': price / rolos, 'unidade': f"{rolos} rolos"} if rolos > 0 else {}

    elif re.search(patterns['folhas'], unit_str_lower):
        folhas = float(re.search(patterns['folhas'], unit_str_lower).group(1))
        return {'preco_por_folha': price / folhas, 'unidade': f"{folhas} folhas"} if folhas > 0 else {}

    return {'preco_unitario': price, 'unidade': unit_str}

# ========================
# Funções Supabase
# ========================
async def get_grupo_id(user_id: int) -> str:
    try:
        resp = supabase.table("usuarios").select("grupo_id").eq("user_id", user_id).execute()
        if resp.data:
            return resp.data[0]['grupo_id']
        novo = str(uuid.uuid4())
        supabase.table("usuarios").insert({"user_id": user_id, "grupo_id": novo}).execute()
        return novo
    except Exception:
        return str(user_id)

async def adicionar_usuario_ao_grupo(novo_user_id: int, codigo_convite: str, convidante_user_id: int = None):
    try:
        resp = supabase.table("usuarios").select("grupo_id").eq("grupo_id", codigo_convite).limit(1).execute()
        if not resp.data:
            return False, "❌ Código de convite inválido."
        grupo_id_para_adicionar = codigo_convite
        check_resp = supabase.table("usuarios").select("grupo_id").eq("user_id", novo_user_id).eq("grupo_id", grupo_id_para_adicionar).execute()
        if check_resp.data:
            return True, f"✅ Você já está no grupo '{grupo_id_para_adicionar}'."
        exists_resp = supabase.table("usuarios").select("user_id").eq("user_id", novo_user_id).execute()
        if exists_resp.data:
            supabase.table("usuarios").update({"grupo_id": grupo_id_para_adicionar}).eq("user_id", novo_user_id).execute()
        else:
            supabase.table("usuarios").insert({"user_id": novo_user_id, "grupo_id": grupo_id_para_adicionar}).execute()
        return True, f"✅ Você foi adicionado ao grupo '{grupo_id_para_adicionar}'!"
    except Exception:
        return False, "❌ Erro ao processar o convite. Tente novamente mais tarde."

# ========================
# Teclados
# ========================
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("✏️ Editar ou Excluir")],
        [KeyboardButton("📋 Listar Produtos"), KeyboardButton("🔍 Pesquisar Produto")],
        [KeyboardButton("ℹ️ Ajuda")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Cancelar")]], resize_keyboard=True)

# ========================
# Handlers
# ========================
# (Todas as funções async: start, help_command, cancel, ask_for_product_data, handle_product_data, confirm_product,
# search_product_input, handle_search_product_input, list_products, ask_for_edit_delete_choice, handle_edit_delete_choice,
# edit_price_callback, handle_edit_price_input, delete_product_callback, confirm_deletion,
# ask_for_invite_code, handle_invite_code_input, inserir_codigo_callback, compartilhar_lista_callback)
# [Insira aqui todas as funções handlers, exatamente como do seu código, sem deixar nenhuma de fora]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    grupo_id = await get_grupo_id(user_id)
    await update.message.reply_text(
        f"🛒 *Bot de Compras Inteligente* 🛒\nSeu grupo compartilhado: `{grupo_id}`\n\nEscolha uma opção ou digite o nome de um produto para pesquisar:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛒 *Como adicionar um produto corretamente:*\n"
        "Use o seguinte formato (uma linha por produto):\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n"
        "*Exemplos:*\n"
        "• Arroz, Branco, Camil, 5 kg, 25.99\n"
        "• Leite, Integral, Italac, 1 L, 4.49\n"
        "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90 ← Sem vírgula entre rolos e metros\n"
        "• Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n"
        "• Ovo, Branco, Grande, 30 und, 16.90\n"
        "• Sabão em Pó, Concentrado, Omo, 1.5 kg, 22.50\n"
        "• Refrigerante, Coca-Cola, 2 L, 8.99\n"
        "• Chocolate, Ao Leite, Nestlé, 90g, 4.50\n"
        "*💡 Dicas:*\n"
        "- Use **ponto como separador decimal** no preço (Ex: 4.99).\n"
        "- Para Papel Higiênico, use o formato: [Quantidade] rolos [Metragem]M (Ex: 12 rolos 30M).\n"
        "- Para produtos com múltiplas embalagens (como '3 tubos de 90g'), descreva assim para que o sistema calcule o custo por unidade.\n"
        "- O sistema automaticamente calculará o **preço por unidade de medida** (Kg, L, ml, g, und, rolo, metro, etc.) e informará qual opção é mais econômica.\n"
        "- Você também pode digitar diretamente o nome de um produto para pesquisar seu preço!\n"
        "- Use os botões abaixo para compartilhar ou acessar listas."
    )
    keyboard = [
        [InlineKeyboardButton("👪 Compartilhar Lista", callback_data="compartilhar_lista")],
        [InlineKeyboardButton("🔐 Inserir Código", callback_data="inserir_codigo")]
    ]
    reply_markup_inline = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(help_text, reply_markup=reply_markup_inline, parse_mode="Markdown")
    await update.message.reply_text("...", reply_markup=main_menu_keyboard())
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# ========================
# Funções para inserir código
# ========================
async def ask_for_invite_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔐 Digite o código do grupo que você recebeu:", reply_markup=cancel_keyboard())
    return AWAIT_INVITE_CODE_INPUT

async def handle_invite_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    codigo_convite = update.message.text.strip()
    user_id = update.effective_user.id
    sucesso, mensagem = await adicionar_usuario_ao_grupo(user_id, codigo_convite)
    if sucesso:
        await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
        return await list_products(update, context)
    else:
        await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
        return MAIN_MENU

async def inserir_codigo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 Digite o código do grupo que você recebeu:")
    await query.message.reply_text("...", reply_markup=cancel_keyboard())
    await query.message.reply_text("...", reply_markup=main_menu_keyboard())
    return AWAIT_INVITE_CODE_INPUT

# ========================
# Função para compartilhar lista
# ========================
async def compartilhar_lista_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        await query.edit_message_text(
            f"🔐 *Compartilhe este código com seus familiares para que eles possam acessar a mesma lista de compras:*\n"
            f"Caso prefira, compartilhe o código abaixo:"
        )
        await query.message.reply_text(f"🔐 Código do grupo: `{grupo_id}`", parse_mode="Markdown")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao gerar convite para user_id {user_id}: {e}")
        await query.edit_message_text("❌ Erro ao gerar convite. Tente novamente mais tarde.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())

# ========================
# Adicionar produto
# ========================
async def ask_for_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Digite os dados do produto no formato:\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n"
        "*Exemplos:*\n"
        "• Arroz, Branco, Camil, 5 kg, 25.99\n"
        "• Leite, Integral, Italac, 1 L, 4.49\n"
        "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90 ← Sem vírgula entre rolos e metros\n"
        "• Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n"
        "• Ovo, Branco, Grande, 30 und, 16.90\n"
        "Ou digite ❌ *Cancelar* para voltar",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown"
    )
    return AWAIT_PRODUCT_DATA

async def handle_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    data = [item.strip() for item in update.message.text.split(",")]
    if len(data) < 5:
        await update.message.reply_text(
            "⚠️ Formato inválido. Você precisa informar pelo menos:\n"
            "*Produto, Tipo, Marca, Unidade, Preço*\n"
            "*Exemplos:*\n"
            "• Arroz, Branco, Camil, 5 kg, 25.99\n"
            "• Leite, Integral, Italac, 1 L, 4.49\n"
            "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90 ← Sem vírgula entre rolos e metros\n"
            "• Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n"
            "• Ovo, Branco, Grande, 30 und, 16.90\n"
            "Ou digite ❌ *Cancelar* para voltar",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown"
        )
        return AWAIT_PRODUCT_DATA
    price_str = data[4].strip()
    price = parse_price(price_str)
    if price is None:
        await update.message.reply_text(
            "⚠️ Preço inválido. Use **ponto como separador decimal** (ex: 4.99).\n"
            "Por favor, digite novamente os dados do produto:",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown"
        )
        return AWAIT_PRODUCT_DATA
    product = {
        'nome': data[0].title(),
        'tipo': data[1].title(),
        'marca': data[2].title(),
        'unidade': data[3].strip(),
        'preco': price_str,
        'observacoes': data[5] if len(data) > 5 else ""
    }
    unit_info = calculate_unit_price(product['unidade'], price)
    logging.info(f"Unit info calculado para {product['nome']}: {unit_info}")
    
    message = f"📦 *Produto*: {product['nome']}\n"
    message += f"🏷️ *Tipo*: {product['tipo']}\n"
    message += f"🏭 *Marca*: {product['marca']}\n"
    message += f"📏 *Unidade*: {product['unidade']}\n"
    message += f"💰 *Preço*: R$ {format_price(price)}\n"
    if product['observacoes']:
        message += f"📝 *Observações*: {product['observacoes']}\n"
    else:
        message += "\n"
    message += "📊 *Cálculo de Preço por Unidade:*\n"
    
    if 'preco_por_kg' in unit_info:
        message += f"📊 *Preço por kg*: R$ {format_price(unit_info['preco_por_kg'])}\n"
    if 'preco_por_100g' in unit_info:
        message += f"📊 *Preço por 100g*: R$ {format_price(unit_info['preco_por_100g'])}\n"
    if 'preco_por_litro' in unit_info:
        message += f"📊 *Preço por litro*: R$ {format_price(unit_info['preco_por_litro'])}\n"
    if 'preco_por_100ml' in unit_info:
        message += f"📊 *Preço por 100ml*: R$ {format_price(unit_info['preco_por_100ml'])}\n"
    if 'preco_por_unidade' in unit_info:
        message += f"📊 *Preço por unidade*: R$ {format_price(unit_info['preco_por_unidade'])}\n"
    if 'preco_por_embalagem' in unit_info:
        message += f"📊 *Preço por embalagem*: R$ {format_price(unit_info['preco_por_embalagem'])}\n"
        if 'preco_por_100' in unit_info:
            message += f"📊 *Preço por 100 (g/ml)*: R$ {format_price(unit_info['preco_por_100'])}\n"
        elif 'preco_por_100_base' in unit_info:
            message += f"📊 *Preço por 100 (g/ml)*: R$ {format_price(unit_info['preco_por_100_base'])}\n"
    if 'preco_por_rolo' in unit_info and 'preco_por_metro' in unit_info:
        message += f"📊 *Preço por rolo*: R$ {format_price(unit_info['preco_por_rolo'])}\n"
        message += f"📊 *Preço por metro*: R$ {format_price(unit_info['preco_por_metro'])}\n"
    if 'preco_por_folha' in unit_info:
        message += f"📊 *Preço por folha*: R$ {format_price(unit_info['preco_por_folha'])}\n"
    message += "\nDigite ✅ *Confirmar* para salvar ou ❌ *Cancelar* para corrigir"
    
    context.user_data['current_product'] = product
    context.user_data['unit_info'] = unit_info
    await update.message.reply_text(
        message,
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ Confirmar"), KeyboardButton("❌ Cancelar")]], resize_keyboard=True),
        parse_mode="Markdown"
    )
    return CONFIRM_PRODUCT

async def confirm_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "✅ Confirmar":
        return await cancel(update, context)
    product = context.user_data.get('current_product')
    unit_info = context.user_data.get('unit_info')
    if not product or not unit_info:
        await update.message.reply_text("❌ Erro ao confirmar produto. Tente novamente.")
        return MAIN_MENU
    user_id = update.effective_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        if 'preco_por_metro' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_metro'])}/metro"
        elif 'preco_por_100g' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_100g'])}/100g"
        elif 'preco_por_kg' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_kg'])}/kg"
        elif 'preco_por_100ml' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_100ml'])}/100ml"
        elif 'preco_por_litro' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_litro'])}/L"
        elif 'preco_por_unidade' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_unidade'])}/unidade"
        elif 'preco_por_embalagem' in unit_info:
            if 'preco_por_100' in unit_info:
                unit_price_str = f"R$ {format_price(unit_info['preco_por_100'])}/100(g/ml)"
            elif 'preco_por_100_base' in unit_info:
                unit_price_str = f"R$ {format_price(unit_info['preco_por_100_base'])}/100(g/ml)"
            else:
                unit_price_str = f"R$ {format_price(unit_info['preco_por_embalagem'])}/embalagem"
        elif 'preco_por_rolo' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_rolo'])}/rolo"
        elif 'preco_por_folha' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_folha'])}/folha"
        else:
            unit_price_str = f"R$ {format_price(parse_price(product['preco']))}/unidade"
        
        novo_produto = {
            "grupo_id": grupo_id,
            "nome": product['nome'],
            "tipo": product['tipo'],
            "marca": product['marca'],
            "unidade": product['unidade'],
            "preco": float(product['preco']),
            "observacoes": product['observacoes'],
            "preco_por_unidade_formatado": unit_price_str,
        }
        response = supabase.table("produtos").insert(novo_produto).execute()
        logging.info(f"Produto salvo no Supabase. Resposta: {response}")
        await update.message.reply_text(
            f"✅ Produto *{product['nome']}* salvo com sucesso na lista do grupo!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Erro ao salvar produto no Supabase: {e}")
        await update.message.reply_text(
            "❌ Erro ao salvar produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

# ========================
# Pesquisar produto
# ========================
async def search_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Digite o nome do produto que você deseja pesquisar:", reply_markup=cancel_keyboard())
    return SEARCH_PRODUCT_INPUT

# ========================
# Modificar a função handle_search_product_input
# ========================
async def handle_search_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    
    # Verificação inicial para evitar que botões sejam tratados como pesquisa
    botoes_especiais = [
        "➕ Adicionar Produto", "✏️ Editar ou Excluir", "📋 Listar Produtos",
        "🔍 Pesquisar Produto", "ℹ️ Ajuda", "❌ Cancelar",
        "👪 Compartilhar Lista", "🔐 Inserir Código", "✅ Confirmar"
    ]
    
    # Se a mensagem for um botão, não faz pesquisa - trata como comando
    if update.message.text.strip() in botoes_especiais:
        # Trata como comando do botão, não como pesquisa
        text = update.message.text.strip()
        if text == "➕ Adicionar Produto":
            return await ask_for_product_data(update, context)
        elif text == "📋 Listar Produtos":
            return await list_products(update, context)
        elif text == "🔍 Pesquisar Produto":
            return await search_product_input(update, context)
        elif text == "ℹ️ Ajuda":
            return await help_command(update, context)
        elif text == "👪 Compartilhar Lista":
            return await compartilhar_lista_callback(update, context)
        elif text == "🔐 Inserir Código":
            return await ask_for_invite_code(update, context)
        elif text == "✏️ Editar ou Excluir":
            return await ask_for_edit_delete_choice(update, context)
        else:
            # Para outros botões, volta ao menu principal
            await update.message.reply_text("⚠️ Por favor, use os botões do menu principal para navegar.", reply_markup=main_menu_keyboard())
            return MAIN_MENU
    
    search_term = update.message.text.strip().lower()
    user_id = update.effective_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        # Corrigido: Selecionar explicitamente os campos necessários
        response = supabase.table("produtos").select("nome, tipo, marca, unidade, preco, observacoes, preco_por_unidade_formatado").eq("grupo_id", grupo_id).ilike("nome", f"%{search_term}%").order("timestamp", desc=True).limit(10).execute()
        produtos_encontrados = response.data
        if not produtos_encontrados:
            await update.message.reply_text(f"📭 Nenhum produto encontrado para '{search_term}'.", reply_markup=main_menu_keyboard())
            return MAIN_MENU
        texto = f"🔍 *Resultados para '{search_term}':*\n"
        for i, produto in enumerate(produtos_encontrados):
            if i > 0: # Adiciona separador antes de cada item, exceto o primeiro
                 texto += "\n--\n"

            # Linha 1: Nome do produto
            texto += f"🏷️ *{produto['nome']}*\n"

            # Linha 2: Tipo, Marca, Unidade
            marca_part = f" | 🏭 {produto['marca']}" if produto.get('marca') and produto['marca'].strip() else ""
            texto += f"  📦 {produto['tipo']}{marca_part} | 📏 {produto['unidade']}\n"

            # Linha 3: Preço e Observações
            obs_part = f"   ({produto['observacoes']})" if produto.get('observacoes') and produto['observacoes'].strip() else ""
            texto += f"  💵 R${format_price(produto['preco'])} |{obs_part}\n"

            # Linhas 4+: Preços por unidade de medida (se disponíveis)
            # Extrair e formatar os preços unitários do campo preco_por_unidade_formatado
            preco_unidade_texto = produto.get('preco_por_unidade_formatado', '')
            if preco_unidade_texto:
                # Exemplo de preco_unidade_texto: "R$ 6,00/kg (Dona)"
                # Padrão para capturar valor e unidade (ex: R$ 6,00/kg)
                padrao_valor_unidade = r"R\$\s*([\d.,]+)\s*/\s*([^\s(]+)"
                match = re.search(padrao_valor_unidade, preco_unidade_texto)
                if match:
                     valor_principal = match.group(1).replace('.', '').replace(',', '.') # Converter para float
                     unidade_principal = match.group(2)
                     try:
                         valor_principal_float = float(valor_principal)
                         texto += f"📊 Preço por {unidade_principal}: R$ {match.group(1)}\n"

                         # Calcular e mostrar preço por 100g ou 100ml se for o caso
                         if unidade_principal.lower() == 'kg':
                             preco_100g = valor_principal_float / 10
                             texto += f"📊 Preço por 100g: R$ {format_price(preco_100g)}\n"
                         elif unidade_principal.lower() == 'g':
                             # Se for por grama, calcula por 100g
                             preco_100g = valor_principal_float * 100
                             texto += f"📊 Preço por 100g: R$ {format_price(preco_100g)}\n"
                         elif unidade_principal.lower() == 'l':
                             preco_100ml = valor_principal_float / 10
                             texto += f"📊 Preço por 100ml: R$ {format_price(preco_100ml)}\n"
                         elif unidade_principal.lower() == 'ml':
                             # Se for por ml, calcula por 100ml
                             preco_100ml = valor_principal_float * 100
                             texto += f"📊 Preço por 100ml: R$ {format_price(preco_100ml)}\n"
                         # Adicione outros casos conforme necessário (und, rolo, metro, etc.)
                     except ValueError:
                         # Se não conseguir converter o valor, mostra o texto original
                         texto += f"📊 {preco_unidade_texto}\n"
                else:
                     # Se não casar com o padrão esperado, mostra o texto original
                     texto += f"📊 {preco_unidade_texto}\n"
            # Se não houver preco_por_unidade_formatado, não mostra nada adicional
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao pesquisar produtos no Supabase para user_id {user_id}: {e}")
        await update.message.reply_text("❌ Erro ao pesquisar produtos.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# ========================
# Corrigir a função list_products
# ========================
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        # Corrigido: Selecionar explicitamente os campos necessários
        response = supabase.table("produtos").select("nome, tipo, marca, unidade, preco, observacoes, preco_por_unidade_formatado").eq("grupo_id", grupo_id).order("timestamp", desc=True).limit(20).execute()
        produtos_do_grupo = response.data
        if not produtos_do_grupo:
            await update.message.reply_text("📭 Nenhum produto na lista ainda.", reply_markup=main_menu_keyboard())
            return MAIN_MENU
        texto = "📋 *Lista de Produtos do seu Grupo:*\n"
        for produto in produtos_do_grupo:
            obs = f" ({produto['observacoes']})" if produto['observacoes'] else ""
            preco_unidade = produto.get('preco_por_unidade_formatado', '')
            
            # Tratamento seguro da marca - só exibe se não estiver vazio
            marca_display = ""
            if produto.get('marca') and produto['marca'].strip():
                marca_display = f" - {produto['marca']}"
                
            # Novo layout: dividido em linhas
            if preco_unidade:
                texto += f"🔹 *{produto['nome']}*{marca_display}\n"
                texto += f"   📦 {produto['tipo']}\n"
                texto += f"   {produto['unidade']} - R${format_price(produto['preco'])}   📊 {preco_unidade}{obs}\n"
            else:
                texto += f"🔹 *{produto['nome']}*{marca_display}\n"
                texto += f"   📦 {produto['tipo']}\n"
                texto += f"   {produto['unidade']} - R${format_price(produto['preco'])}{obs}\n"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao listar produtos do Supabase: {e}")
        await update.message.reply_text("❌ Erro ao acessar a lista.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# ========================
# Editar/Excluir produto
# ========================
async def ask_for_edit_delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✏️ *Editar/Excluir Produto*\n"
        "Digite o *nome* do produto que você deseja editar ou excluir:",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown"
    )
    return AWAIT_EDIT_DELETE_CHOICE

# Editar/Excluir produto (Versão 02 Corrigida)
# ========================
async def handle_edit_delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    search_term = update.message.text.strip().title()
    user_id = update.effective_user.id
    try:
        grupo_id = await get_grupo_id(user_id)

        # Função auxiliar para buscar todos os produtos com paginação
        def fetch_all_products():
            all_data = []
            offset = 0
            page_size = 101 # Ajuste conforme necessário, 100 é um valor comum
            while True:
                response = (supabase.table("produtos")
                            .select("id, nome, tipo, marca, unidade, preco, observacoes")
                            .eq("grupo_id", grupo_id)
                            .ilike("nome", f"%{search_term}%") # Usar ilike para busca parcial
                            .order("timestamp", desc=True)
                            .range(offset, offset + page_size - 1)
                            .execute())
                batch = response.data
                if not batch:
                    break
                all_data.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size
            return all_data

        matching_products = fetch_all_products()

        if not matching_products:
            await update.message.reply_text(
                f"📭 Nenhum produto encontrado com o nome '{search_term}'.",
                reply_markup=main_menu_keyboard()
            )
            return MAIN_MENU

        # Se só encontrar 1, vai direto para as opções de editar/excluir
        if len(matching_products) == 1:
            product = matching_products[0]
            context.user_data['editing_product'] = product
            keyboard = [
                [InlineKeyboardButton("✏️ Editar Preço", callback_data=f"edit_price_{product['id']}")],
                [InlineKeyboardButton("🗑️ Excluir", callback_data=f"delete_{product['id']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"✏️ *Produto Selecionado:*\n"
                f"📦 *{product['nome']}*\n"
                f"🏷️ *Tipo:* {product['tipo']}\n"
                f"🏭 *Marca:* {product['marca']}\n"
                f"📏 *Unidade:* {product['unidade']}\n"
                f"💰 *Preço:* R$ {format_price(product['preco'])}\n"
                f"📝 *Observações:* {product['observacoes'] or ''}\n"
                f"Escolha uma ação:",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return AWAIT_EDIT_PRICE # Retornar o estado correto aqui

        # Correção: Sempre listar produtos encontrados como texto com numeração
        context.user_data['pending_products'] = matching_products # Armazena a lista para uso posterior
        texto_lista = f"🔍 Encontrei {len(matching_products)} produto(s) com o nome semelhante a '{search_term}'.\n\n"
        texto_lista += "Por favor, digite o *número* do produto que deseja editar ou excluir:\n\n"

        for idx, prod in enumerate(matching_products):
            marca = f" - {prod['marca']}" if prod.get('marca') and prod['marca'].strip() else ""
            preco_str = format_price(prod['preco'])
            obs = f" ({prod['observacoes']})" if prod.get('observacoes') and prod['observacoes'].strip() else ""
            # Formato: 1. Nome - Marca (Tipo, Unidade, R$Preco) (Obs)
            texto_lista += f"{idx + 1}. *{prod['nome']}*{marca} ({prod['tipo']}, {prod['unidade']}, R${preco_str}){obs}\n"

        # Correção: Garantir botão de Cancelar na tela de escolha
        await update.message.reply_text(texto_lista, parse_mode="Markdown", reply_markup=cancel_keyboard())
        # Muda o estado para esperar o número digitado pelo usuário
        return AWAIT_ENTRY_CHOICE

    except Exception as e:
        logging.error(f"Erro ao buscar produto '{search_term}' para edição/exclusão: {e}", exc_info=True) # Adiciona exc_info para mais detalhes
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU
# ========================
# Processar escolha por número (Correção: Editar/Excluir)
# ========================
async def process_entry_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o número do produto escolhido para edição/exclusão."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    try:
        choice = int(update.message.text)
    except ValueError:
        await update.message.reply_text("⚠️ Entrada inválida. Por favor, digite apenas o *número* do produto.", reply_markup=cancel_keyboard(), parse_mode="Markdown")
        return AWAIT_ENTRY_CHOICE # Permanece no mesmo estado

    pending_products = context.user_data.get('pending_products')
    if not pending_products or not isinstance(pending_products, list):
         await update.message.reply_text("❌ Erro ao recuperar a lista de produtos. Tente novamente.", reply_markup=main_menu_keyboard())
         return MAIN_MENU

    if choice < 1 or choice > len(pending_products):
        await update.message.reply_text(f"⚠️ Número inválido. Escolha um número entre 1 e {len(pending_products)}.", reply_markup=cancel_keyboard())
        # Reenvia a lista para facilitar
        # (Opcional: reenviar a lista aqui, mas pode ser verboso. Só pede o número novamente)
        return AWAIT_ENTRY_CHOICE # Permanece no mesmo estado

    selected_product = pending_products[choice - 1]
    context.user_data['editing_product'] = selected_product # Reutiliza a chave editing_product

    # Criar teclado inline para Editar/Excluir o produto selecionado
    keyboard = [
        [InlineKeyboardButton("✏️ Editar Preço", callback_data=f"edit_price_{selected_product['id']}")],
        [InlineKeyboardButton("🗑️ Excluir", callback_data=f"delete_{selected_product['id']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    marca_display = f" - {selected_product['marca']}" if selected_product.get('marca') and selected_product['marca'].strip() else ""
    obs_display = f"\n📝 *Observações:* {selected_product['observacoes']}" if selected_product.get('observacoes') and selected_product['observacoes'].strip() else ""

    await update.message.reply_text(
        f"✏️ *Produto Selecionado:*\n"
        f"📦 *{selected_product['nome']}*{marca_display}\n"
        f"🏷️ *Tipo:* {selected_product['tipo']}\n"
        f"📏 *Unidade:* {selected_product['unidade']}\n"
        f"💰 *Preço:* R$ {format_price(selected_product['preco'])}"
        f"{obs_display}\n\n"
        f"Escolha uma ação:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    # Sai do estado AWAIT_ENTRY_CHOICE e permite que os callbacks tomem o controle
    return MAIN_MENU
    
# ========================
# Callbacks para editar/excluir
# ========================
async def edit_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = query.data.split("_")[2]
    user_id = query.from_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        response = supabase.table("produtos").select("*").eq("id", product_id).eq("grupo_id", grupo_id).limit(1).execute()
        product = response.data[0] if response.data else None
        if not product:
            await query.edit_message_text("❌ Produto não encontrado ou você não tem permissão para editá-lo.")
            await query.message.reply_text("...", reply_markup=main_menu_keyboard())
            return MAIN_MENU
        context.user_data['editing_product'] = product
        await query.edit_message_text(
            f"✏️ *Editar Preço do Produto:*\n"
            f"📦 *{product['nome']}*\n"
            f"🏷️ *Tipo:* {product['tipo']}\n"
            f"🏭 *Marca:* {product['marca']}\n"
            f"📏 *Unidade:* {product['unidade']}\n"
            f"💰 *Preço Atual:* R$ {format_price(product['preco'])}\n"
            f"Digite o *novo preço* (use **ponto como separador decimal**):",
            parse_mode="Markdown"
        )
        await query.message.reply_text("...", reply_markup=cancel_keyboard())
        return AWAIT_EDIT_PRICE
    except Exception as e:
        logging.error(f"Erro ao preparar edição de preço para produto ID {product_id}: {e}")
        await query.edit_message_text("❌ Erro ao preparar edição. Tente novamente mais tarde.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())
        return MAIN_MENU

async def handle_edit_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    new_price_str = update.message.text.strip()
    new_price = parse_price(new_price_str)
    if new_price is None:
        await update.message.reply_text(
            "⚠️ Preço inválido. Use **ponto como separador decimal** (ex: 4.99).\n"
            "Por favor, digite novamente o novo preço:",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown"
        )
        return AWAIT_EDIT_PRICE
    product = context.user_data.get('editing_product')
    if not product:
        await update.message.reply_text("❌ Erro ao editar preço. Tente novamente.")
        return MAIN_MENU
    user_id = update.effective_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        check_response = supabase.table("produtos").select("id, preco_por_unidade_formatado").eq("id", product['id']).eq("grupo_id", grupo_id).limit(1).execute()
        if not check_response.data:
            await update.message.reply_text("❌ Você não tem permissão para editar este produto.")
            return MAIN_MENU
        unit_info = calculate_unit_price(product['unidade'], new_price)
        if 'preco_por_metro' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_metro'])}/metro"
        elif 'preco_por_100g' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100g'])}/100g"
        elif 'preco_por_kg' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_kg'])}/kg"
        elif 'preco_por_100ml' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100ml'])}/100ml"
        elif 'preco_por_litro' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_litro'])}/L"
        elif 'preco_por_unidade' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_unidade'])}/unidade"
        elif 'preco_por_embalagem' in unit_info:
            if 'preco_por_100' in unit_info:
                new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100'])}/100(g/ml)"
            elif 'preco_por_100_base' in unit_info:
                new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100_base'])}/100(g/ml)"
            else:
                new_unit_price_str = f"R$ {format_price(unit_info['preco_por_embalagem'])}/embalagem"
        elif 'preco_por_rolo' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_rolo'])}/rolo"
        elif 'preco_por_folha' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_folha'])}/folha"
        else:
            new_unit_price_str = f"R$ {format_price(new_price)}/unidade"
        
        updated_product = {
            "preco": new_price,
            "preco_por_unidade_formatado": new_unit_price_str,
        }
        response = supabase.table("produtos").update(updated_product).eq("id", product['id']).execute()
        logging.info(f"Produto ID {product['id']} atualizado no Supabase. Resposta: {response}")
        await update.message.reply_text(
            f"✅ Preço do produto *{product['nome']}* atualizado com sucesso para R$ {format_price(new_price)}!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Erro ao atualizar preço do produto ID {product['id']}: {e}")
        await update.message.reply_text(
            "❌ Erro ao atualizar preço. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

async def delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = query.data.split("_")[1]
    user_id = query.from_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        response = supabase.table("produtos").select("*").eq("id", product_id).eq("grupo_id", grupo_id).limit(1).execute()
        product = response.data[0] if response.data else None
        if not product:
            await query.edit_message_text("❌ Produto não encontrado ou você não tem permissão para excluí-lo.")
            await query.message.reply_text("...", reply_markup=main_menu_keyboard())
            return MAIN_MENU
        context.user_data['deleting_product'] = product
        await query.edit_message_text(
            f"🗑️ *Excluir Produto:*\n"
            f"📦 *{product['nome']}*\n"
            f"🏷️ *Tipo:* {product['tipo']}\n"
            f"🏭 *Marca:* {product['marca']}\n"
            f"📏 *Unidade:* {product['unidade']}\n"
            f"💰 *Preço:* R$ {format_price(product['preco'])}\n"
            f"📝 *Observações:* {product['observacoes']}\n"
            f"Tem certeza que deseja excluir este produto?",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ Confirmar"), KeyboardButton("❌ Cancelar")]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return CONFIRM_DELETION
    except Exception as e:
        logging.error(f"Erro ao preparar exclusão para produto ID {product_id}: {e}")
        await query.edit_message_text("❌ Erro ao preparar exclusão. Tente novamente mais tarde.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())
        return MAIN_MENU

async def confirm_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "✅ Confirmar":
        return await cancel(update, context)
    product = context.user_data.get('deleting_product')
    if not product:
        await update.message.reply_text("❌ Erro ao confirmar exclusão. Tente novamente.")
        return MAIN_MENU
    user_id = update.effective_user.id
    try:
        grupo_id = await get_grupo_id(user_id)
        check_response = supabase.table("produtos").select("id").eq("id", product['id']).eq("grupo_id", grupo_id).limit(1).execute()
        if not check_response.data:
            await update.message.reply_text("❌ Você não tem permissão para excluir este produto.")
            return MAIN_MENU
        response = supabase.table("produtos").delete().eq("id", product['id']).execute()
        logging.info(f"Produto ID {product['id']} excluído do Supabase. Resposta: {response}")
        await update.message.reply_text(
            f"✅ Produto *{product['nome']}* excluído com sucesso!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Erro ao excluir produto ID {product['id']}: {e}")
        await update.message.reply_text(
            "❌ Erro ao excluir produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

# ========================
# Webhook handler
# ========================
@app.route("/webhook", methods=["POST"])
def webhook():
    global bot_application, bot_event_loop
    if bot_application is None or bot_event_loop is None:
        logging.warning("Bot application ou event loop ainda não está pronto para receber atualizações.")
        return "Service Unavailable", 503

    json_data = request.get_json()
    if not json_data:
        logging.warning("Requisição POST /webhook sem dados JSON.")
        return "Bad Request", 400

    try:
        update = Update.de_json(json_data, bot_application.bot)
        asyncio.run_coroutine_threadsafe(
            bot_application.process_update(update),
            bot_event_loop
        )
    except Exception as e:
        logging.error(f"Erro ao agendar atualização no loop de eventos: {e}", exc_info=True)
        return "Internal Server Error", 500

    return "OK", 200

# ========================
# Inicialização do bot e registro de handlers
# ========================
async def select_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = query.data.split("_")[2]  # select_prod_{id}

    pending_products = context.user_data.get('pending_products', [])
    product = next((p for p in pending_products if p['id'] == product_id), None)

    if not product:
        await query.edit_message_text("❌ Produto não encontrado.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())
        return MAIN_MENU

    context.user_data['editing_product'] = product

    keyboard = [
        [InlineKeyboardButton("✏️ Editar Preço", callback_data=f"edit_price_{product['id']}")],
        [InlineKeyboardButton("🗑️ Excluir", callback_data=f"delete_{product['id']}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🗑️ *Excluir Produto:*\n" \
        f"📦 *{product['nome']}*\n" \
        f"🏷️ *Tipo:* {product['tipo']}\n" \
        f"🏭 *Marca:* {product['marca']}\n" \
        f"📏 *Unidade:* {product['unidade']}\n" \
        f"💰 *Preço:* R$ {format_price(product['preco'])}\n" \
        f"📝 *Observações:* {product['observacoes']}\n" \
        f"Tem certeza que deseja excluir este produto?",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ Confirmar"), KeyboardButton("❌ Cancelar")]], resize_keyboard=True),
        parse_mode="Markdown"
    )
    return AWAIT_EDIT_PRICE

async def start_bot():
    global bot_application
    bot_application = Application.builder().token(TOKEN).build()

    # ========================
    # Handlers de comandos
    # ========================
    bot_application.add_handler(CommandHandler("start", start))
    bot_application.add_handler(CommandHandler("help", help_command))
    bot_application.add_handler(CommandHandler("cancel", cancel))

    # ========================
    # CallbackQueryHandler (botões inline)
    # ========================
    bot_application.add_handler(CallbackQueryHandler(compartilhar_lista_callback, pattern="^compartilhar_lista$"))
    bot_application.add_handler(CallbackQueryHandler(inserir_codigo_callback, pattern="^inserir_codigo$"))
    bot_application.add_handler(CallbackQueryHandler(edit_price_callback, pattern="^edit_price_"))
    bot_application.add_handler(CallbackQueryHandler(delete_product_callback, pattern="^delete_"))
    bot_application.add_handler(CallbackQueryHandler(select_product_callback, pattern="^select_prod_"))
    
    # ========================
    # ConversationHandler (fluxos de conversa)
    # ========================
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_for_product_data),
            MessageHandler(filters.Regex("^✏️ Editar ou Excluir$"), ask_for_edit_delete_choice),
            MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
            MessageHandler(filters.Regex("^🔍 Pesquisar Produto$"), search_product_input),
            MessageHandler(filters.Regex("^ℹ️ Ajuda$"), help_command),
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_product_input),
            ],
            AWAIT_PRODUCT_DATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_data),
            ],
            CONFIRM_PRODUCT: [
                MessageHandler(filters.Regex("^✅ Confirmar$|^❌ Cancelar$"), confirm_product),
            ],
            AWAIT_EDIT_DELETE_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_delete_choice),
            ],
            AWAIT_EDIT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_price_input),
            ],
            SEARCH_PRODUCT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_product_input),
            ],
            CONFIRM_DELETION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_deletion),
            ],
            AWAIT_INVITE_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_invite_code_input),
            ],
            AWAIT_INVITE_CODE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_invite_code_input),
            ],
            # Correção: Novo estado para esperar a escolha numérica do produto
            AWAIT_ENTRY_CHOICE: [
                MessageHandler(filters.Regex("^❌ Cancelar$"), cancel), # Permite cancelar
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_entry_choice), # Handler para o número
        ],
    },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^❌ Cancelar$"), cancel),
        ],
    )
    bot_application.add_handler(conv_handler)

    # Inicialização padrão
    await bot_application.initialize()
    await bot_application.start()
    url = f"{WEBHOOK_DOMAIN}/webhook"
    await bot_application.bot.set_webhook(url=url)
    logging.info(f"Webhook do Telegram setado para: {url}")

# ========================
# Função para rodar Flask
# ========================
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)

# ========================
# Main
# ========================
if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
    logging.info("Iniciando bot com webhook via Flask e Python 3.13.4")

    # Crie o event loop principal e salve na global
    bot_event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_event_loop)

    # Rode Flask em thread separada
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logging.info("Servidor Flask iniciado em thread separada.")

    # Inicialize o bot (e set o webhook) no event loop principal
    init_task = bot_event_loop.create_task(start_bot())
    bot_event_loop.run_until_complete(init_task)
    logging.info("Bot initialized and webhook set.")

    # Deixe o event loop ativo enquanto Flask roda
    logging.info("Mantendo o loop de eventos principal ativo com loop.run_forever()...")
    try:
        bot_event_loop.run_forever()
    except KeyboardInterrupt:
        logging.info("Recebido KeyboardInterrupt. Encerrando...")
    finally:
        # Limpeza (opcional)
        pending = asyncio.all_tasks(loop=bot_event_loop)
        for task in pending:
            task.cancel()
        bot_event_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        bot_event_loop.close()
        logging.info("Loop de eventos encerrado.")
    logging.info("Bot encerrado.")
    logging.info("=" * 50)

