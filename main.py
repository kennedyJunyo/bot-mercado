# main.py

import os
import logging
import asyncio
import re
import uuid
from threading import Thread
from datetime import datetime
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)
# === NOVO: Import do Supabase ===
from supabase import create_client, Client

# === CONFIGURAÇÕES ===
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# === REMOVIDO: Configurações do Google Sheets ===
# SPREADSHEET_ID = "1ShIhn1IQj8txSUshTJh_ypmzoyvIO40HLNi1ZN28rIo"
# ABA_NOME = "Página1" # Aba principal de produtos
# ABA_USUARIOS = "Usuarios" # Nova aba para mapear user_id -> grupo_id
# CRED_FILE = "/etc/secrets/credentials.json" # Certifique-se de que este caminho está correto no Render

# === NOVO: Configurações do Supabase ===
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# === NOVO: Inicialização do Cliente Supabase ===
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL e SUPABASE_KEY devem ser definidos nas variáveis de ambiente.")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === ESTADOS DO CONVERSATION HANDLER === (mantém como está)
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

# === REMOVIDO: Funções do Google Sheets (get_sheet, get_usuarios_sheet, get_produtos_sheet) ===

# === NOVO: FUNÇÕES PARA INTERAGIR COM O SUPABASE ===

async def get_grupo_id(user_id: int) -> str:
    """Obtém o grupo_id de um usuário. Se não existir, cria um novo grupo."""
    try:
        # Tenta encontrar o usuário na tabela 'usuarios'
        response = supabase.table("usuarios").select("grupo_id").eq("user_id", user_id).execute()
        data = response.data

        if data: # <--- LINHA 69 CORRIGIDA CORRETAMENTE
            # Usuário encontrado, retorna o grupo_id existente
            logging.info(f"Grupo encontrado para user_id {user_id}: {data[0]['grupo_id']}")
            return data[0]['grupo_id']
        else:
            # Usuário não encontrado, cria um novo grupo e usuário
            novo_grupo_id = str(uuid.uuid4())
            insert_response = supabase.table("usuarios").insert({"user_id": user_id, "grupo_id": novo_grupo_id}).execute()
            logging.info(f"Novo grupo criado para user_id {user_id}: {novo_grupo_id}. Resposta: {insert_response}")
            return novo_grupo_id

    except Exception as e:
        logging.error(f"Erro ao obter/criar grupo_id para user_id {user_id}: {e}")
        # Fallback: usar o próprio user_id como grupo_id (menos ideal com Supabase)
        return str(user_id)


# === NOVA FUNÇÃO CORRIGIDA: ADICIONAR USUÁRIO AO GRUPO (adaptada) ===
async def adicionar_usuario_ao_grupo(novo_user_id: int, codigo_convite: str, convidante_user_id: int = None):
    """Adiciona um novo usuário a um grupo baseado no código de convite (que é o grupo_id)."""
    try:
        # 1. Verificar se o codigo_convite (grupo_id) existe na tabela 'usuarios'
        response = supabase.table("usuarios").select("grupo_id").eq("grupo_id", codigo_convite).limit(1).execute()
        if not response.data:
            return False, "❌ Código de convite inválido."

        grupo_id_para_adicionar = codigo_convite

        # 2. Verificar se o usuário já está NO MESMO grupo
        check_response = supabase.table("usuarios").select("grupo_id").eq("user_id", novo_user_id).eq("grupo_id", grupo_id_para_adicionar).execute()
        if check_response.data:
            return True, f"✅ Você já está no grupo '{grupo_id_para_adicionar}'."

        # 3. Verificar se o usuário já existe (em outro grupo)
        exists_response = supabase.table("usuarios").select("user_id").eq("user_id", novo_user_id).execute()
        if exists_response.data:
            # Atualiza o grupo_id do usuário existente
            update_response = supabase.table("usuarios").update({"grupo_id": grupo_id_para_adicionar}).eq("user_id", novo_user_id).execute()
            logging.info(f"Usuário {novo_user_id} atualizado para o grupo {grupo_id_para_adicionar}. Resposta: {update_response}")
        else:
            # Adiciona novo usuário
            insert_response = supabase.table("usuarios").insert({"user_id": novo_user_id, "grupo_id": grupo_id_para_adicionar}).execute()
            logging.info(f"Usuário {novo_user_id} adicionado ao grupo {grupo_id_para_adicionar}. Resposta: {insert_response}")

        logging.info(f"Notificação: Novo membro {novo_user_id} entrou no grupo {grupo_id_para_adicionar}. Convidado por {convidante_user_id}")
        return True, f"✅ Você foi adicionado ao grupo '{grupo_id_para_adicionar}'!"

    except Exception as e:
        logging.error(f"Erro ao adicionar usuário {novo_user_id} ao grupo com convite {codigo_convite}: {e}")
        return False, "❌ Erro ao processar o convite. Tente novamente mais tarde."


# === REMOVIDO: listar_membros_do_grupo (não está sendo usada no código atual) ===

# === FUNÇÕES AUXILIARES === (mantém como está)
def format_price(price):
    """Formata float para string com vírgula decimal (para exibição)"""
    # Garante que o input seja float
    try:
        price_float = float(price)
    except (ValueError, TypeError):
        return "0,00"
    return "{:,.2f}".format(price_float).replace(".", ",")

def parse_price(price_str):
    """Converte string de preço para float."""
    try:
        return float(price_str.replace(',', '.'))
    except ValueError:
        return None

# === FUNÇÃO CORRIGIDA: CALCULAR PREÇO POR UNIDADE === (mantém como está)
# (A função calculate_unit_price permanece a mesma)

# === TECLADOS === (mantém como está)
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("✏️ Editar/Excluir")],
        [KeyboardButton("🔍 Pesquisar Produto"), KeyboardButton("📋 Listar Produtos")],
        [KeyboardButton("👪 Compartilhar Lista"), KeyboardButton("🔐 Inserir Código")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Cancelar")]], resize_keyboard=True)

# === HANDLERS === (adaptados onde interagem com dados)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
    grupo_id = await get_grupo_id(user_id)
    await update.message.reply_text(
        f"🛒 *Bot de Compras Inteligente* 🛒\n"
        f"Seu grupo compartilhado: `{grupo_id}`\n\n"
        f"Escolha uma opção ou digite o nome de um produto para pesquisar:",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (mantém como está)
    help_text = (
        "🛒 *Como adicionar um produto corretamente:*\n"
        "Use o seguinte formato (uma linha por produto):\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n\n"
        "*Exemplos:*\n"
        "• Arroz, Branco, Camil, 5 kg, 25.99\n"
        "• Leite, Integral, Italac, 1 L, 4.49\n"
        "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90 ← Sem vírgula entre rolos e metros\n"
        "• Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n"
        "• Ovo, Branco, Grande, 30 und, 16.90\n"
        "• Sabão em Pó, Concentrado, Omo, 1.5 kg, 22.50\n"
        "• Refrigerante, Coca-Cola, 2 L, 8.99\n"
        "• Chocolate, Ao Leite, Nestlé, 90g, 4.50\n\n"
        "*💡 Dicas:*\n"
        "- Use **ponto como separador decimal** no preço (Ex: 4.99).\n"
        "- Para Papel Higiênico, use o formato: [Quantidade] rolos [Metragem]M (Ex: 12 rolos 30M).\n"
        "- Para produtos com múltiplas embalagens (como '3 tubos de 90g'), descreva assim para que o sistema calcule o custo por unidade.\n"
        "- O sistema automaticamente calculará o **preço por unidade de medida** (Kg, L, ml, g, und, rolo, metro, etc.) e informará qual opção é mais econômica.\n"
        "- Você também pode digitar diretamente o nome de um produto para pesquisar seu preço!\n"
        "- Use os botões abaixo para compartilhar ou acessar listas."
    )
    # Criar um teclado inline com os botões de compartilhar e inserir código
    keyboard = [
        [InlineKeyboardButton("👪 Compartilhar Lista", callback_data="compartilhar_lista")],
        [InlineKeyboardButton("🔐 Inserir Código", callback_data="inserir_codigo")] # Novo botão
    ]
    reply_markup_inline = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(help_text, reply_markup=reply_markup_inline, parse_mode="Markdown")
    # Manter o teclado principal também
    await update.message.reply_text("...", reply_markup=main_menu_keyboard())
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (mantém como está)
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# === NOVAS FUNÇÕES PARA INSERIR CÓDIGO === (adaptadas)
async def ask_for_invite_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pede ao usuário para digitar o código de convite."""
    await update.message.reply_text("🔐 Digite o código do grupo que você recebeu:", reply_markup=cancel_keyboard())
    return AWAIT_INVITE_CODE_INPUT

async def handle_invite_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o código de convite digitado pelo usuário."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    codigo_convite = update.message.text.strip()
    user_id = update.effective_user.id

    # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
    sucesso, mensagem = await adicionar_usuario_ao_grupo(user_id, codigo_convite)

    if sucesso:
        await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
        # Após entrar, mostra a lista de produtos do novo grupo
        return await list_products(update, context)
    else:
        await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
        return MAIN_MENU

# === NOVA FUNÇÃO CALLBACK PARA O BOTÃO INLINE === (mantém como está, usa get_grupo_id)
async def inserir_codigo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'Inserir Código' no teclado inline."""
    query = update.callback_query
    await query.answer() # Responde ao clique do botão
    # Edita a mensagem para mostrar o prompt de digitar o código
    await query.edit_message_text("🔐 Digite o código do grupo que você recebeu:")
    # Como não podemos mudar o teclado facilmente aqui, vamos enviar uma nova mensagem
    await query.message.reply_text("...", reply_markup=cancel_keyboard())
    # E uma nova mensagem com o teclado principal
    await query.message.reply_text("...", reply_markup=main_menu_keyboard())
    # O estado será gerenciado pelo MessageHandler no teclado principal
    return AWAIT_INVITE_CODE_INPUT # Inicia o fluxo de digitação de código

# =================================================
# === NOVA FUNÇÃO: COMPARTILHAR LISTA === (adaptada)
async def compartilhar_lista_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'Compartilhar Lista'."""
    query = update.callback_query
    await query.answer() # Responde ao clique do botão
    user_id = query.from_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # Mensagem explicativa
        await query.edit_message_text(
            f"🔐 *Compartilhe este código com seus familiares para que eles possam acessar a mesma lista de compras:*\n\n"
            f"Caso prefira, compartilhe o código abaixo:"
        )
        # Segunda mensagem: código
        # Como não podemos editar para enviar outra mensagem, enviamos uma nova
        await query.message.reply_text(f"🔐 Código do grupo: `{grupo_id}`", parse_mode="Markdown")
        # Reenvia o menu principal
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())

    except Exception as e:
        logging.error(f"Erro ao gerar convite para user_id {user_id}: {e}")
        await query.edit_message_text("❌ Erro ao gerar convite. Tente novamente mais tarde.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())

# === ADICIONAR PRODUTO === (mantém ask_for_product_data e handle_product_data como está)
# Modificando apenas confirm_product

async def ask_for_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (mantém como está)
    await update.message.reply_text(
        "📝 Digite os dados do produto no formato:\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n\n"
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
    # ... (mantém como está)
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    data = [item.strip() for item in update.message.text.split(",")]
    if len(data) < 5:
        await update.message.reply_text(
            "⚠️ Formato inválido. Você precisa informar pelo menos:\n"
            "*Produto, Tipo, Marca, Unidade, Preço*\n\n"
            "*Exemplos:*\n"
            "• Arroz, Branco, Camil, 5 kg, 25.99\n"
            "• Leite, Integral, Italac, 1 L, 4.49\n"
            "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90 ← Sem vírgula entre rolos e metros\n"
            "• Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n"
            "• Ovo, Branco, Grande, 30 und, 16.90\n\n"
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
        'preco': price_str, # Mantém como string para compatibilidade com partes do código
        'observacoes': data[5] if len(data) > 5 else ""
    }

    # Calcular preço por unidade
    unit_info = calculate_unit_price(product['unidade'], price)
    logging.info(f"Unit info calculado para {product['nome']}: {unit_info}")

    # Montar mensagem de confirmação com os preços calculados
    message = f"📦 *Produto*: {product['nome']}\n"
    message += f"🏷️ *Tipo*: {product['tipo']}\n"
    message += f"🏭 *Marca*: {product['marca']}\n"
    message += f"📏 *Unidade*: {product['unidade']}\n"
    message += f"💰 *Preço*: R$ {format_price(price)}\n"
    if product['observacoes']:
        message += f"📝 *Observações*: {product['observacoes']}\n\n"
    else:
        message += "\n"

    message += "📊 *Cálculo de Preço por Unidade:*\n"
    # Exibir os preços calculados de forma mais clara
    if 'preco_por_kg' in unit_info:
        message += f"📊 *Preço por kg*: R$ {format_price(unit_info['preco_por_kg'])}\n"
    if 'preco_por_100g' in unit_info:
        message += f"📊 *Preço por 100g*: R$ {format_price(unit_info['preco_por_100g'])}\n"
    if 'preco_por_litro' in unit_info:
        message += f"📊 *Preço por litro*: R$ {format_price(unit_info['preco_por_litro'])}\n"
    if 'preco_por_100ml' in unit_info: # Inclui preço por 100ml para L e ml
        message += f"📊 *Preço por 100ml*: R$ {format_price(unit_info['preco_por_100ml'])}\n"
    if 'preco_por_unidade' in unit_info:
        message += f"📊 *Preço por unidade*: R$ {format_price(unit_info['preco_por_unidade'])}\n"
    if 'preco_por_embalagem' in unit_info:
         # Para produtos com múltiplas embalagens
         message += f"📊 *Preço por embalagem*: R$ {format_price(unit_info['preco_por_embalagem'])}\n"
         if 'preco_por_100' in unit_info:
             message += f"📊 *Preço por 100 (g/ml)*: R$ {format_price(unit_info['preco_por_100'])}\n"
         elif 'preco_por_100_base' in unit_info:
             message += f"📊 *Preço por 100 (g/ml)*: R$ {format_price(unit_info['preco_por_100_base'])}\n"
    # === Exibição específica para Papel Higiênico ===
    if 'preco_por_rolo' in unit_info and 'preco_por_metro' in unit_info:
        # Caso específico para Papel Higiênico (ou outros com múltiplas unidades)
        message += f"📊 *Preço por rolo*: R$ {format_price(unit_info['preco_por_rolo'])}\n"
        message += f"📊 *Preço por metro*: R$ {format_price(unit_info['preco_por_metro'])}\n"
    # =================================================
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
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # Formatar a string do preço por unidade para salvar (mantém como está)
        # Prioriza os cálculos mais relevantes para comparação
        if 'preco_por_metro' in unit_info: # Papel Higiênico
            unit_price_str = f"R$ {format_price(unit_info['preco_por_metro'])}/metro"
        elif 'preco_por_100g' in unit_info: # Produtos em gramas
            unit_price_str = f"R$ {format_price(unit_info['preco_por_100g'])}/100g"
        elif 'preco_por_kg' in unit_info: # Produtos em kg
            unit_price_str = f"R$ {format_price(unit_info['preco_por_kg'])}/kg"
        elif 'preco_por_100ml' in unit_info: # Produtos em ml ou L convertido
             unit_price_str = f"R$ {format_price(unit_info['preco_por_100ml'])}/100ml"
        elif 'preco_por_litro' in unit_info: # Fallback para L se 100ml não estiver
             unit_price_str = f"R$ {format_price(unit_info['preco_por_litro'])}/L"
        elif 'preco_por_unidade' in unit_info: # Produtos unitários
            unit_price_str = f"R$ {format_price(unit_info['preco_por_unidade'])}/unidade"
        elif 'preco_por_embalagem' in unit_info: # Produtos com múltiplas embalagens
             if 'preco_por_100' in unit_info:
                 unit_price_str = f"R$ {format_price(unit_info['preco_por_100'])}/100(g/ml)"
             elif 'preco_por_100_base' in unit_info:
                 unit_price_str = f"R$ {format_price(unit_info['preco_por_100_base'])}/100(g/ml)"
             else:
                 unit_price_str = f"R$ {format_price(unit_info['preco_por_embalagem'])}/embalagem"
        elif 'preco_por_rolo' in unit_info: # Rolos simples
            unit_price_str = f"R$ {format_price(unit_info['preco_por_rolo'])}/rolo"
        elif 'preco_por_folha' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_folha'])}/folha"
        else:
            unit_price_str = f"R$ {format_price(parse_price(product['preco']))}/unidade"

        # timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S") # Opcional, usar o default now() do banco

        # === SALVANDO NO SUPABASE ===
        novo_produto = {
            "grupo_id": grupo_id,
            "nome": product['nome'],
            "tipo": product['tipo'],
            "marca": product['marca'],
            "unidade": product['unidade'],
            "preco": float(product['preco']), # Converter para número para o banco
            "observacoes": product['observacoes'],
            "preco_por_unidade_formatado": unit_price_str,
            # "timestamp": timestamp # Considere usar o default now() do banco ou um objeto datetime
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


# === LISTAR PRODUTOS === (adaptada)
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === CONSULTANDO O SUPABASE ===
        # Seleciona todos os campos (*), filtra por grupo_id, ordena por timestamp DESC, limita a 20
        # NOTA: A coluna no Supabase é 'timestamp', não 'Timestamp'
        response = supabase.table("produtos").select("*").eq("grupo_id", grupo_id).order("timestamp", desc=True).limit(20).execute()
        produtos_do_grupo = response.data

        if not produtos_do_grupo:
            await update.message.reply_text("📭 Nenhum produto na lista ainda.", reply_markup=main_menu_keyboard())
            return MAIN_MENU

        texto = "📋 *Lista de Produtos do seu Grupo:*\n\n"
        # Mostra os últimos 20 registros
        for produto in produtos_do_grupo: # Acessa os dados como dicionários
            # NOTA: Os nomes das colunas no Supabase são os mesmos definidos na tabela
            obs = f" ({produto['observacoes']})" if produto['observacoes'] else ""
            # Usa format_price para formatar o preço vindo do banco (número)
            texto += f"🔹 *{produto['nome']}* - {produto['marca']} - {produto['unidade']} - R${format_price(produto['preco'])}{obs}\n"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao listar produtos do Supabase: {e}")
        await update.message.reply_text("❌ Erro ao acessar a lista.", reply_markup=main_menu_keyboard())
    return MAIN_MENU


# === FLASK + WEBHOOK === (mantém como está, incluindo o /healthz)
# A ÚNICA ALTERAÇÃO ESTÁ NA FUNÇÃO ABAIXO
app = Flask(__name__)
application = None

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.route("/")
def home():
    return "🛒 Bot de Compras está no ar!", 200

# >>>>> FUNÇÃO WEBHOOK MODIFICADA PARA SER SÍNCRONA <<<<<
@app.route("/webhook", methods=["POST"])
def webhook(): # <--- Removido 'async'
    json_data = request.get_json()
    update = Update.de_json(json_data, application.bot)
    # <--- Removido 'await' e usado o método síncrono
    application.process_update(update)
    return "OK", 200
# >>>>> FIM DA ALTERAÇÃO <<<<<

# === MAIN === (mantém como está)
async def start_bot():
    global application

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_for_product_data),
                MessageHandler(filters.Regex("^🔍 Pesquisar Produto$"), lambda u, c: SEARCH_PRODUCT_INPUT), # Placeholder
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^✏️ Editar/Excluir$"), lambda u, c: AWAIT_EDIT_DELETE_CHOICE), # Placeholder
                MessageHandler(filters.Regex("^👪 Compartilhar Lista$"), lambda u, c: compartilhar_lista_callback(u, c)),
                MessageHandler(filters.Regex("^🔐 Inserir Código$"), ask_for_invite_code),
                MessageHandler(filters.COMMAND, help_command), # Para /help ou outros comandos
            ],
            AWAIT_PRODUCT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_data)],
            CONFIRM_PRODUCT: [MessageHandler(filters.Regex("^(✅ Confirmar|❌ Cancelar)$"), confirm_product)],
            AWAIT_INVITE_CODE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_invite_code_input)],
            # Adicione outros estados conforme necessário
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancelar$"), cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(compartilhar_lista_callback, pattern="^compartilhar_lista$"))
    application.add_handler(CallbackQueryHandler(inserir_codigo_callback, pattern="^inserir_codigo$"))

    await application.initialize()
    await application.bot.set_webhook(url=f"{os.environ['RENDER_EXTERNAL_URL']}/webhook")
    await application.start()

    while True:
        await asyncio.sleep(3600)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    logging.info("Iniciando bot com webhook via Flask")
    Thread(target=run_flask).start()
    asyncio.run(start_bot())
