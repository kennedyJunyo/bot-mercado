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

# === NOVO: Configurações do Supabase ===
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# === NOVO: Inicialização do Cliente Supabase ===
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL e SUPABASE_KEY devem ser definidos nas variáveis de ambiente.")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === ESTADOS DO CONVERSATION HANDLER ===
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

# === CONFIGURAÇÃO DETALHADA DO LOGGING ===
# Configura o logger principal com nível INFO e formatação detalhada
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
    level=logging.INFO # Mude para DEBUG para logs ainda mais detalhados
)
logger = logging.getLogger(__name__) # Logger específico para este módulo

# === NOVO: FUNÇÕES PARA INTERAGIR COM O SUPABASE ===

async def get_grupo_id(user_id: int) -> str:
    """Obtém o grupo_id de um usuário. Se não existir, cria um novo grupo."""
    try:
        logger.debug(f"Tentando encontrar usuário {user_id} no Supabase...")
        # Tenta encontrar o usuário na tabela 'usuarios'
        response = supabase.table("usuarios").select("grupo_id").eq("user_id", user_id).execute()
        data = response.data

        if data: # <--- LINHA 69 CORRIGIDA CORRETAMENTE
            # Usuário encontrado, retorna o grupo_id existente
            grupo_id = data[0]['grupo_id']
            logger.info(f"Grupo encontrado para user_id {user_id}: {grupo_id}")
            return grupo_id
        else:
            # Usuário não encontrado, cria um novo grupo e usuário
            logger.info(f"Usuário {user_id} não encontrado. Criando novo grupo...")
            novo_grupo_id = str(uuid.uuid4())
            insert_response = supabase.table("usuarios").insert({"user_id": user_id, "grupo_id": novo_grupo_id}).execute()
            logger.info(f"Novo grupo criado para user_id {user_id}: {novo_grupo_id}. Resposta: {insert_response}")
            return novo_grupo_id

    except Exception as e:
        logger.error(f"Erro ao obter/criar grupo_id para user_id {user_id}: {e}", exc_info=True)
        # Fallback: usar o próprio user_id como grupo_id (menos ideal com Supabase)
        fallback_id = str(user_id)
        logger.warning(f"Usando fallback ID {fallback_id} para user_id {user_id}")
        return fallback_id


# === NOVA FUNÇÃO CORRIGIDA: ADICIONAR USUÁRIO AO GRUPO (adaptada) ===
async def adicionar_usuario_ao_grupo(novo_user_id: int, codigo_convite: str, convidante_user_id: int = None):
    """Adiciona um novo usuário a um grupo baseado no código de convite (que é o grupo_id)."""
    try:
        logger.debug(f"Verificando código de convite '{codigo_convite}' para usuário {novo_user_id}...")
        # 1. Verificar se o codigo_convite (grupo_id) existe na tabela 'usuarios'
        response = supabase.table("usuarios").select("grupo_id").eq("grupo_id", codigo_convite).limit(1).execute()
        if not response.data: # <--- LINHA 102 CORRIGIDA
            logger.warning(f"Código de convite inválido: '{codigo_convite}'")
            return False, "❌ Código de convite inválido."

        grupo_id_para_adicionar = codigo_convite
        logger.debug(f"Código de convite válido. Grupo alvo: {grupo_id_para_adicionar}")

        # 2. Verificar se o usuário já está NO MESMO grupo
        logger.debug(f"Verificando se usuário {novo_user_id} já está no grupo {grupo_id_para_adicionar}...")
        check_response = supabase.table("usuarios").select("grupo_id").eq("user_id", novo_user_id).eq("grupo_id", grupo_id_para_adicionar).execute()
        if check_response.data: # <--- LINHA 110 CORRIGIDA
            logger.info(f"Usuário {novo_user_id} já está no grupo {grupo_id_para_adicionar}")
            return True, f"✅ Você já está no grupo '{grupo_id_para_adicionar}'."

        # 3. Verificar se o usuário já existe (em outro grupo)
        logger.debug(f"Verificando se usuário {novo_user_id} existe em outro grupo...")
        exists_response = supabase.table("usuarios").select("user_id").eq("user_id", novo_user_id).execute()
        if exists_response.data: # <--- LINHA 117 CORRIGIDA
            # Atualiza o grupo_id do usuário existente
            logger.info(f"Atualizando grupo do usuário {novo_user_id} para {grupo_id_para_adicionar}...")
            update_response = supabase.table("usuarios").update({"grupo_id": grupo_id_para_adicionar}).eq("user_id", novo_user_id).execute()
            logger.info(f"Usuário {novo_user_id} atualizado para o grupo {grupo_id_para_adicionar}. Resposta: {update_response}")
        else:
            # Adiciona novo usuário
            logger.info(f"Adicionando novo usuário {novo_user_id} ao grupo {grupo_id_para_adicionar}...")
            insert_response = supabase.table("usuarios").insert({"user_id": novo_user_id, "grupo_id": grupo_id_para_adicionar}).execute()
            logger.info(f"Usuário {novo_user_id} adicionado ao grupo {grupo_id_para_adicionar}. Resposta: {insert_response}")

        logger.info(f"Notificação: Novo membro {novo_user_id} entrou no grupo {grupo_id_para_adicionar}. Convidado por {convidante_user_id}")
        return True, f"✅ Você foi adicionado ao grupo '{grupo_id_para_adicionar}'!"

    except Exception as e:
        logger.error(f"Erro ao adicionar usuário {novo_user_id} ao grupo com convite {codigo_convite}: {e}", exc_info=True)
        return False, "❌ Erro ao processar o convite. Tente novamente mais tarde."


# === FUNÇÕES AUXILIARES ===
def format_price(price):
    """Formata float para string com vírgula decimal (para exibição)"""
    try:
        price_float = float(price)
        return "{:,.2f}".format(price_float).replace(".", ",")
    except (ValueError, TypeError) as e:
        logger.warning(f"Erro ao formatar preço '{price}': {e}")
        return "0,00"

def parse_price(price_str):
    """Converte string de preço para float."""
    try:
        return float(price_str.replace(',', '.'))
    except ValueError as e:
        logger.warning(f"Erro ao parsear preço '{price_str}': {e}")
        return None

# === FUNÇÃO CORRIGIDA: CALCULAR PREÇO POR UNIDADE ===
# (A função calculate_unit_price permanece a mesma)
def calculate_unit_price(unit_str, price):
    """Calcula preço por unidade de medida com base na nova estrutura"""
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
        if rolos > 0 and metros > 0:
            preco_por_rolo = price / rolos
            preco_por_metro = price / metros
            return {
                'preco_por_rolo': preco_por_rolo,
                'preco_por_metro': preco_por_metro,
                'quantidade_rolos': rolos,
                'metros_totais': metros,
                'unidade': f"{int(rolos) if rolos.is_integer() else rolos} rolos, {int(metros) if metros.is_integer() else metros}m"
            }

    elif re.search(patterns['multiplas_embalagens'], unit_str_lower):
        match = re.search(patterns['multiplas_embalagens'], unit_str_lower)
        qtd_embalagens = float(match.group(1))
        tipo_embalagem = match.group(2)
        tamanho_unidade = float(match.group(3))
        unidade_medida = match.group(4).lower()
        if qtd_embalagens > 0:
            total_unidades = qtd_embalagens * tamanho_unidade
            preco_por_embalagem = price / qtd_embalagens
            if unidade_medida in ['g', 'ml']:
                preco_por_100 = price / total_unidades * 100
                return {
                    'preco_por_embalagem': preco_por_embalagem,
                    'preco_por_100': preco_por_100,
                    'unidade': f"{int(qtd_embalagens) if qtd_embalagens.is_integer() else qtd_embalagens} {tipo_embalagem} de {int(tamanho_unidade) if tamanho_unidade.is_integer() else tamanho_unidade}{unidade_medida}"
                }
            elif unidade_medida in ['kg', 'l']:
                preco_por_unidade_base = price / total_unidades
                preco_por_100_base = preco_por_unidade_base * 100
                return {
                    'preco_por_embalagem': preco_por_embalagem,
                    'preco_por_unidade_base': preco_por_unidade_base,
                    'preco_por_100_base': preco_por_100_base,
                    'unidade': f"{int(qtd_embalagens) if qtd_embalagens.is_integer() else qtd_embalagens} {tipo_embalagem} de {int(tamanho_unidade) if tamanho_unidade.is_integer() else tamanho_unidade}{unidade_medida}"
                }
            else:
                return {
                    'preco_por_embalagem': preco_por_embalagem,
                    'preco_por_unidade': preco_por_embalagem,
                    'unidade': f"{int(qtd_embalagens) if qtd_embalagens.is_integer() else qtd_embalagens} {tipo_embalagem} de {int(tamanho_unidade) if tamanho_unidade.is_integer() else tamanho_unidade}{unidade_medida}"
                }

    elif re.search(patterns['kg'], unit_str_lower):
        match = re.search(patterns['kg'], unit_str_lower)
        kg = float(match.group(1))
        if kg > 0:
            return {'preco_por_kg': price / kg, 'unidade': f"{int(kg) if kg.is_integer() else kg}kg"}

    elif re.search(patterns['g'], unit_str_lower):
        match = re.search(patterns['g'], unit_str_lower)
        g = float(match.group(1))
        if g > 0:
            preco_por_100g = price / g * 100
            return {'preco_por_100g': preco_por_100g, 'unidade': f"{int(g) if g.is_integer() else g}g"}

    elif re.search(patterns['l'], unit_str_lower):
        match = re.search(patterns['l'], unit_str_lower)
        l = float(match.group(1))
        if l > 0:
            preco_por_litro = price / l
            total_ml = l * 1000
            preco_por_100ml = price / total_ml * 100 if total_ml > 0 else 0
            return {
                'preco_por_litro': preco_por_litro,
                'preco_por_100ml': preco_por_100ml,
                'unidade': f"{int(l) if l.is_integer() else l}L"
            }

    elif re.search(patterns['ml'], unit_str_lower):
        match = re.search(patterns['ml'], unit_str_lower)
        ml = float(match.group(1))
        if ml > 0:
            preco_por_100ml = price / ml * 100
            return {'preco_por_100ml': preco_por_100ml, 'unidade': f"{int(ml) if ml.is_integer() else ml}ml"}

    elif re.search(patterns['und'], unit_str_lower):
        match = re.search(patterns['und'], unit_str_lower)
        und = float(match.group(1))
        if und > 0:
            return {'preco_por_unidade': price / und, 'unidade': f"{int(und) if und.is_integer() else und} und"}

    elif re.search(patterns['rolo_simples'], unit_str_lower):
        match = re.search(patterns['rolo_simples'], unit_str_lower)
        rolos = float(match.group(1))
        if rolos > 0:
            return {'preco_por_rolo': price / rolos, 'unidade': f"{int(rolos) if rolos.is_integer() else rolos} rolos"}

    elif re.search(patterns['folhas'], unit_str_lower):
         match = re.search(patterns['folhas'], unit_str_lower)
         folhas = float(match.group(1))
         if folhas > 0:
             return {'preco_por_folha': price / folhas, 'unidade': f"{int(folhas) if folhas.is_integer() else folhas} folhas"}

    # Se nenhum padrão foi encontrado, retorna o preço unitário
    return {'preco_unitario': price, 'unidade': unit_str}


# === TECLADOS ===
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("✏️ Editar/Excluir")],
        [KeyboardButton("🔍 Pesquisar Produto"), KeyboardButton("📋 Listar Produtos")],
        [KeyboardButton("👪 Compartilhar Lista"), KeyboardButton("🔐 Inserir Código")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Cancelar")]], resize_keyboard=True)

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Handler /start foi chamado!")
    user_id = update.effective_user.id
    logger.info(f"User ID: {user_id}")
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)
        logger.info(f"Grupo ID obtido: {grupo_id}")
        await update.message.reply_text(
            f"🛒 *Bot de Compras Inteligente* 🛒\n"
            f"Seu grupo compartilhado: `{grupo_id}`\n\n"
            f"Escolha uma opção ou digite o nome de um produto para pesquisar:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        logger.info("Mensagem de resposta do /start enviada!")
    except Exception as e:
        logger.error(f"Erro no handler /start para user_id {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Ocorreu um erro. Tente novamente mais tarde.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Handler /help foi chamado!")
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
    keyboard = [
        [InlineKeyboardButton("👪 Compartilhar Lista", callback_data="compartilhar_lista")],
        [InlineKeyboardButton("🔐 Inserir Código", callback_data="inserir_codigo")]
    ]
    reply_markup_inline = InlineKeyboardMarkup(keyboard)
    try:
        await update.message.reply_text(help_text, reply_markup=reply_markup_inline, parse_mode="Markdown")
        await update.message.reply_text("...", reply_markup=main_menu_keyboard())
        logger.info("Mensagem de ajuda enviada com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem de ajuda: {e}", exc_info=True)
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Operação cancelada pelo usuário.")
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# === NOVAS FUNÇÕES PARA INSERIR CÓDIGO ===
async def ask_for_invite_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Pedindo código de convite ao usuário.")
    await update.message.reply_text("🔐 Digite o código do grupo que você recebeu:", reply_markup=cancel_keyboard())
    return AWAIT_INVITE_CODE_INPUT

async def handle_invite_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Processando código de convite inserido pelo usuário.")
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    codigo_convite = update.message.text.strip()
    user_id = update.effective_user.id
    logger.info(f"Usuário {user_id} tentando ingressar com código: '{codigo_convite}'")

    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        sucesso, mensagem = await adicionar_usuario_ao_grupo(user_id, codigo_convite)
        logger.info(f"Resultado do convite para {user_id}: Sucesso={sucesso}, Mensagem={mensagem}")

        if sucesso:
            await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
            # Após entrar, mostra a lista de produtos do novo grupo
            logger.debug("Convite aceito. Mostrando lista de produtos.")
            return await list_products(update, context) # Ou chame diretamente list_products
        else:
            await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
            logger.debug("Convite recusado ou erro. Retornando ao menu principal.")
            return MAIN_MENU
    except Exception as e:
        logger.error(f"Erro inesperado ao processar convite para user_id {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Erro inesperado. Tente novamente mais tarde.", reply_markup=main_menu_keyboard())
        return MAIN_MENU

# === NOVA FUNÇÃO CALLBACK PARA O BOTÃO INLINE ===
async def inserir_codigo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Callback 'Inserir Código' acionado.")
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 Digite o código do grupo que você recebeu:")
    await query.message.reply_text("...", reply_markup=cancel_keyboard())
    await query.message.reply_text("...", reply_markup=main_menu_keyboard())
    return AWAIT_INVITE_CODE_INPUT

# =================================================
# === NOVA FUNÇÃO: COMPARTILHAR LISTA ===
async def compartilhar_lista_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Callback 'Compartilhar Lista' acionado.")
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)
        logger.info(f"Gerando convite para grupo {grupo_id} solicitado por {user_id}")

        await query.edit_message_text(
            f"🔐 *Compartilhe este código com seus familiares para que eles possam acessar a mesma lista de compras:*\n\n"
            f"Caso prefira, compartilhe o código abaixo:"
        )
        await query.message.reply_text(f"🔐 Código do grupo: `{grupo_id}`", parse_mode="Markdown")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())
        logger.info("Código de convite enviado com sucesso.")

    except Exception as e:
        logger.error(f"Erro ao gerar convite para user_id {user_id}: {e}", exc_info=True)
        await query.edit_message_text("❌ Erro ao gerar convite. Tente novamente mais tarde.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())

# === ADICIONAR PRODUTO ===
async def ask_for_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Pedindo dados do produto ao usuário.")
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
    logger.debug("Processando dados do produto inseridos pelo usuário.")
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    data = [item.strip() for item in update.message.text.split(",")]
    if len(data) < 5:
        logger.warning("Dados do produto incompletos.")
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
        logger.warning(f"Preço inválido fornecido: '{price_str}'")
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
        'preco': price_str, # Mantém como string para compatibilidade
        'observacoes': data[5] if len(data) > 5 else ""
    }

    # Calcular preço por unidade
    unit_info = calculate_unit_price(product['unidade'], price)
    logger.debug(f"Unit info calculado para {product['nome']}: {unit_info}")

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
    logger.debug("Confirmação de produto recebida.")
    if update.message.text != "✅ Confirmar":
        return await cancel(update, context)

    product = context.user_data.get('current_product')
    unit_info = context.user_data.get('unit_info')
    if not product or not unit_info:
        logger.error("Erro ao confirmar produto: dados ausentes em user_data.")
        await update.message.reply_text("❌ Erro ao confirmar produto. Tente novamente.", reply_markup=main_menu_keyboard())
        return MAIN_MENU

    user_id = update.effective_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)
        logger.info(f"Salvando produto '{product['nome']}' para grupo {grupo_id} (usuário {user_id})")

        # Formatar a string do preço por unidade para salvar
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

        # === SALVANDO NO SUPABASE ===
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
        logger.info(f"Produto salvo no Supabase. Resposta: {response}")

        await update.message.reply_text(
            f"✅ Produto *{product['nome']}* salvo com sucesso na lista do grupo!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        logger.info(f"Produto '{product['nome']}' confirmado e salvo com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao salvar produto no Supabase para user_id {user_id}: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Erro ao salvar produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU


# === LISTAR PRODUTOS ===
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("Listando produtos para o usuário.")
    user_id = update.effective_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)
        logger.info(f"Buscando produtos para grupo {grupo_id} (usuário {user_id})")

        # === CONSULTANDO O SUPABASE ===
        response = supabase.table("produtos").select("*").eq("grupo_id", grupo_id).order("timestamp", desc=True).limit(20).execute()
        produtos_do_grupo = response.data

        if not produtos_do_grupo:
            logger.info("Nenhum produto encontrado para o grupo.")
            await update.message.reply_text("📭 Nenhum produto na lista ainda.", reply_markup=main_menu_keyboard())
            return MAIN_MENU

        texto = "📋 *Lista de Produtos do seu Grupo:*\n\n"
        for produto in produtos_do_grupo:
            obs = f" ({produto['observacoes']})" if produto['observacoes'] else ""
            texto += f"🔹 *{produto['nome']}* - {produto['marca']} - {produto['unidade']} - R${format_price(produto['preco'])}{obs}\n"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        logger.info(f"Lista de {len(produtos_do_grupo)} produtos enviada.")
    except Exception as e:
        logger.error(f"Erro ao listar produtos do Supabase para user_id {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Erro ao acessar a lista.", reply_markup=main_menu_keyboard())
    return MAIN_MENU


# === FLASK + WEBHOOK ===
# Mantém Flask apenas para /, /healthz e manter o Render feliz com um servidor HTTP
app = Flask(__name__)
# A instância do Application será criada e configurada no start_bot
bot_application = None
# O loop de eventos principal será armazenado aqui
bot_event_loop = None

@app.route("/healthz")
def healthz():
    logger.debug("Endpoint /healthz acessado.")
    return "OK", 200

@app.route("/")
def home():
    logger.debug("Endpoint raiz (/) acessado.")
    return "🛒 Bot de Compras está no ar!", 200

# >>>>> FUNÇÃO WEBHOOK CORRIGIDA PARA USAR run_coroutine_threadsafe <<<<<
@app.route("/webhook", methods=["POST"])
def webhook():
    global bot_application, bot_event_loop
    # Verifica se o bot está pronto para receber atualizações
    if bot_application is None or bot_event_loop is None:
        logger.warning("Bot application ou event loop ainda não está pronto para receber atualizações.")
        return "Service Unavailable", 503 # Service Unavailable

    json_data = request.get_json()
    if not json_data:
        logger.warning("Requisição POST /webhook sem dados JSON.")
        return "Bad Request", 400 # Bad Request

    # Cria o objeto Update a partir do JSON
    update = Update.de_json(json_data, bot_application.bot)
    logger.info(f"Webhook recebido: Update ID {update.update_id}, Tipo: {type(update)}")

    # Agendar o processamento da atualização no loop de eventos principal
    # Isso é thread-safe e a forma correta de integrar Flask com o loop de eventos do bot
    try:
        # Usando run_coroutine_threadsafe para agendar no loop principal
        future = asyncio.run_coroutine_threadsafe(
            bot_application.process_update(update), # <<<--- Usando process_update diretamente
            bot_event_loop # <<<--- Passando o loop global
        )
        # Opcional: Adicionar callback para verificar o resultado (não bloqueante)
        # future.add_done_callback(lambda f: logger.debug(f"Update {update.update_id} scheduled. Result: {f.result() if not f.exception() else f.exception()}"))
        logger.debug(f"Update {update.update_id} agendado para processamento no loop de eventos.")
    except Exception as e:
        logger.error(f"Erro ao agendar atualização no loop de eventos: {e}", exc_info=True)
        return "Internal Server Error", 500 # Internal Server Error

    # Retorna 200 OK imediatamente para o Telegram
    logger.debug("Respondendo 200 OK ao Telegram.")
    return "OK", 200
# >>>>> FIM DA ALTERAÇÃO <<<<<

# === MAIN ===
# Função para configurar e iniciar o bot (parte assíncrona)
async def start_bot():
    global bot_application, bot_event_loop
    # Armazena a referência ao loop de eventos atual (rodando na thread principal)
    bot_event_loop = asyncio.get_running_loop()
    logger.info("Loop de eventos principal armazenado.")

    bot_application = Application.builder().token(TOKEN).build()
    logger.info("Application do bot criada.")

    # Configurar handlers (seu código existente para conv_handler, etc.)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CommandHandler("start", start), # Permite /start dentro do estado MAIN_MENU
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

    bot_application.add_handler(conv_handler)
    bot_application.add_handler(CallbackQueryHandler(compartilhar_lista_callback, pattern="^compartilhar_lista$"))
    bot_application.add_handler(CallbackQueryHandler(inserir_codigo_callback, pattern="^inserir_codigo$"))
    logger.info("Handlers adicionados.")

    await bot_application.initialize()
    logger.info("Application inicializada.")

    # Configura o webhook usando o próprio Application
    WEBHOOK_URL = f"{os.environ['RENDER_EXTERNAL_URL']}/webhook"
    await bot_application.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

    await bot_application.start()
    logger.info("Application started. Bot está ativo e ouvindo webhooks.")
    # A aplicação agora está pronta para receber atualizações via webhook
    # O loop de eventos principal (rodando asyncio.run(start_bot())) continua ativo

# Função para rodar o Flask em thread separada
def run_flask():
    logger.info("Iniciando servidor Flask em thread separada...")
    # Flask escuta na porta especificada pelo Render
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False) # Debug False em produção

# >>>>> BLOCO PRINCIPAL REESCRITO PARA PYTHON 3.13.4 <<<<<
if __name__ == "__main__":
    # O logger básico já foi configurado no início do arquivo
    logger.info("=" * 50)
    logger.info("Iniciando bot com webhook via Flask e Python 3.13.4")
    logger.info("=" * 50)

    # 1. Inicia o servidor Flask em uma thread separada
    # Isso libera a thread principal para rodar o loop de eventos asyncio
    flask_thread = Thread(target=run_flask, name="FlaskThread")
    flask_thread.daemon = True # Permite que o processo termine mesmo se a thread Flask estiver ativa
    flask_thread.start()
    logger.info("Servidor Flask iniciado em thread separada.")

    # 2. Executa a lógica assíncrona do bot na thread principal
    # asyncio.run() gerencia o loop de eventos para nós
    try:
        logger.info("Iniciando loop de eventos principal com asyncio.run(start_bot())...")
        asyncio.run(start_bot())
        # O código abaixo de asyncio.run só executa se start_bot() retornar
        # ou lançar uma exceção não tratada dentro do loop.
        logger.info("Bot encerrado normalmente (start_bot retornou).")
    except KeyboardInterrupt:
        logger.info("Recebido KeyboardInterrupt. Encerrando...")
    except Exception as e:
        logger.critical(f"Erro fatal no bot: {e}", exc_info=True) # Nível CRITICAL para erros fatais
    finally:
        # O loop de eventos criado por asyncio.run() é automaticamente fechado aqui
        logger.info("Loop de eventos encerrado.")
        
    logger.info("Bot encerrado.")
    logger.info("=" * 50)
# >>>>> FIM DO BLOCO PRINCIPAL <<<<<
