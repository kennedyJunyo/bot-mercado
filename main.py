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
# Definindo os estados de forma clara e explícita
# Adicionando estados para o fluxo de compartilhamento
# Corrigido para 11 estados
(
    MAIN_MENU,
    AWAIT_PRODUCT_DATA,
    CONFIRM_PRODUCT,
    AWAIT_EDIT_DELETE_CHOICE,
    AWAIT_EDIT_PRICE,
    AWAIT_DELETION_CHOICE,
    CONFIRM_DELETION,
    SEARCH_PRODUCT_INPUT,
    AWAIT_ENTRY_CHOICE, # Novo estado para escolher entre criar ou entrar
    AWAIT_INVITE_CODE, # Novo estado para pedir o código de convite
    AWAIT_INVITE_CODE_INPUT # Novo estado para processar o código digitado
) = range(11)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# === NOVO: Variáveis Globais para o Loop e Application ===
# Armazena a instância do Application e o loop de eventos principal
# para acesso no webhook
bot_application = None
bot_event_loop = None
# === FIM DAS VARIÁVEIS GLOBAIS ===

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


# === NOVA FUNÇÃO CORRIGIDA: ADICIONAR USUÁRIO AO GRUPO ===
async def adicionar_usuario_ao_grupo(novo_user_id: int, codigo_convite: str, convidante_user_id: int = None):
    """Adiciona um novo usuário a um grupo baseado no código de convite (que é o grupo_id)."""
    try:
        # 1. Verificar se o codigo_convite (grupo_id) existe na tabela 'usuarios'
        response = supabase.table("usuarios").select("grupo_id").eq("grupo_id", codigo_convite).limit(1).execute()
        if not response.data: # <--- LINHA 102 CORRIGIDA
            return False, "❌ Código de convite inválido."

        grupo_id_para_adicionar = codigo_convite

        # 2. Verificar se o usuário já está NO MESMO grupo
        check_response = supabase.table("usuarios").select("grupo_id").eq("user_id", novo_user_id).eq("grupo_id", grupo_id_para_adicionar).execute()
        if check_response.data: # <--- LINHA 110 CORRIGIDA
            return True, f"✅ Você já está no grupo '{grupo_id_para_adicionar}'."

        # 3. Verificar se o usuário já existe (em outro grupo)
        exists_response = supabase.table("usuarios").select("user_id").eq("user_id", novo_user_id).execute()
        if exists_response.data: # <--- LINHA 117 CORRIGIDA
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

# === FUNÇÕES AUXILIARES ===
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

# === FUNÇÃO CORRIGIDA: CALCULAR PREÇO POR UNIDADE ===
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
            # Retorna ambos os cálculos E o preço unitário por rolo para comparações completas
            return {
                'preco_por_rolo': preco_por_rolo,
                'preco_por_metro': preco_por_metro,
                'quantidade_rolos': rolos, # Armazena a quantidade para comparações mais complexas se necessário
                'metros_totais': metros,   # Armazena a metragem total
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
                # Para kg ou L, calcula por unidade e por 100 da unidade base
                preco_por_unidade_base = price / total_unidades # Preço por kg ou L
                preco_por_100_base = preco_por_unidade_base * 100 # Preço por 100g ou 100ml
                return {
                    'preco_por_embalagem': preco_por_embalagem,
                    'preco_por_unidade_base': preco_por_unidade_base,
                    'preco_por_100_base': preco_por_100_base,
                    'unidade': f"{int(qtd_embalagens) if qtd_embalagens.is_integer() else qtd_embalagens} {tipo_embalagem} de {int(tamanho_unidade) if tamanho_unidade.is_integer() else tamanho_unidade}{unidade_medida}"
                }
            else:
                return {
                    'preco_por_embalagem': preco_por_embalagem,
                    'preco_por_unidade': preco_por_embalagem, # Fallback genérico
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
            total_ml = l * 1000 # Converte litros para ml
            preco_por_100ml = price / total_ml * 100 if total_ml > 0 else 0
            return {
                'preco_por_litro': preco_por_litro,
                'preco_por_100ml': preco_por_100ml, # ✅ Nova linha: calcula preço por 100ml para Litros
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
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# === NOVAS FUNÇÕES PARA INSERIR CÓDIGO ===
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

# === NOVA FUNÇÃO CALLBACK PARA O BOTÃO INLINE ===
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
# === NOVA FUNÇÃO: COMPARTILHAR LISTA ===
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

# === ADICIONAR PRODUTO ===
async def ask_for_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# === PESQUISAR PRODUTO ===
async def search_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pede ao usuário para digitar o nome do produto a ser pesquisado."""
    await update.message.reply_text("🔍 Digite o nome do produto que você deseja pesquisar:", reply_markup=cancel_keyboard())
    return SEARCH_PRODUCT_INPUT

async def handle_search_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o nome do produto digitado para pesquisa."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    search_term = update.message.text.strip().lower()
    user_id = update.effective_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === CONSULTANDO O SUPABASE COM ILIKE ===
        # Busca produtos cujo nome contenha o termo (case-insensitive)
        # NOTA: A coluna no Supabase é 'nome', não 'Produto'
        response = supabase.table("produtos").select("*").eq("grupo_id", grupo_id).ilike("nome", f"%{search_term}%").order("timestamp", desc=True).limit(10).execute()
        produtos_encontrados = response.data

        if not produtos_encontrados:
            await update.message.reply_text(f"📭 Nenhum produto encontrado para '{search_term}'.", reply_markup=main_menu_keyboard())
            return MAIN_MENU

        texto = f"🔍 *Resultados para '{search_term}':*\n\n"
        # Mostra os últimos 10 registros encontrados
        for produto in produtos_encontrados: # Acessa os dados como dicionários
            # NOTA: Os nomes das colunas no Supabase são os mesmos definidos na tabela
            obs = f" ({produto['observacoes']})" if produto['observacoes'] else ""
            # Usa format_price para formatar o preço vindo do banco (número)
            texto += f"🔹 *{produto['nome']}* - {produto['marca']} - {produto['unidade']} - R${format_price(produto['preco'])}{obs}\n"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao pesquisar produtos no Supabase para user_id {user_id}: {e}")
        await update.message.reply_text("❌ Erro ao pesquisar produtos.", reply_markup=main_menu_keyboard())
    return MAIN_MENU


# === LISTAR PRODUTOS ===
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


# === EDITAR/EXCLUIR PRODUTO ===
async def ask_for_edit_delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pede ao usuário para escolher entre editar ou excluir."""
    await update.message.reply_text(
        "✏️ *Editar/Excluir Produto*\n"
        "Digite o *nome* do produto que você deseja editar ou excluir:",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown"
    )
    return AWAIT_EDIT_DELETE_CHOICE

async def handle_edit_delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o nome do produto para editar/excluir."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    product_name = update.message.text.strip().title()
    user_id = update.effective_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === BUSCAR PRODUTO PELO NOME NO GRUPO ===
        # NOTA: A coluna no Supabase é 'nome', não 'Produto'
        response = supabase.table("produtos").select("*").eq("grupo_id", grupo_id).eq("nome", product_name).order("timestamp", desc=True).limit(1).execute()
        matching_products = response.data

        if not matching_products:
            await update.message.reply_text(
                f"📭 Produto '{product_name}' não encontrado no seu grupo.",
                reply_markup=main_menu_keyboard()
            )
            return MAIN_MENU

        product = matching_products[0] # Pega o primeiro (mais recente)
        context.user_data['editing_product'] = product # Armazena o produto completo

        # === MENU DE EDIÇÃO/EXCLUSÃO ===
        keyboard = [
            [InlineKeyboardButton("✏️ Editar Preço", callback_data=f"edit_price_{product['id']}")], # Usar o ID único do Supabase
            [InlineKeyboardButton("🗑️ Excluir", callback_data=f"delete_{product['id']}")] # Usar o ID único do Supabase
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✏️ *Produto Selecionado:*\n"
            f"📦 *{product['nome']}*\n"
            f"🏷️ *Tipo:* {product['tipo']}\n"
            f"🏭 *Marca:* {product['marca']}\n"
            f"📏 *Unidade:* {product['unidade']}\n"
            f"💰 *Preço:* R$ {format_price(product['preco'])}\n"
            f"📝 *Observações:* {product['observacoes']}\n\n"
            f"Escolha uma ação:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return AWAIT_EDIT_PRICE # Ou um novo estado específico para gerenciar callbacks

    except Exception as e:
        logging.error(f"Erro ao buscar produto '{product_name}' para edição/exclusão: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar o produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

# === CALLBACKS PARA EDITAR/EXCLUIR ===
async def edit_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'Editar Preço'."""
    query = update.callback_query
    await query.answer()
    # Extrair o ID do produto do callback_data
    product_id = query.data.split("_")[2] # Ex: "edit_price_12345" -> ["edit", "price", "12345"]
    user_id = query.from_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === BUSCAR PRODUTO PELO ID E GRUPO ===
        # Isso garante que o usuário só edite produtos do próprio grupo
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
            f"💰 *Preço Atual:* R$ {format_price(product['preco'])}\n\n"
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
    """Processa o novo preço digitado pelo usuário."""
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
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === ATUALIZAR PREÇO NO SUPABASE ===
        # Garantir que o produto pertence ao grupo do usuário
        check_response = supabase.table("produtos").select("id").eq("id", product['id']).eq("grupo_id", grupo_id).limit(1).execute()
        if not check_response.data:
            await update.message.reply_text("❌ Você não tem permissão para editar este produto.")
            return MAIN_MENU

        # Recalcular preço por unidade com o novo preço
        unit_info = calculate_unit_price(product['unidade'], new_price)
        # Formatar o novo preço por unidade
        if 'preco_por_metro' in unit_info: # Papel Higiênico
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_metro'])}/metro"
        elif 'preco_por_100g' in unit_info: # Produtos em gramas
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100g'])}/100g"
        elif 'preco_por_kg' in unit_info: # Produtos em kg
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_kg'])}/kg"
        elif 'preco_por_100ml' in unit_info: # Produtos em ml ou L convertido
             new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100ml'])}/100ml"
        elif 'preco_por_litro' in unit_info: # Fallback para L se 100ml não estiver
             new_unit_price_str = f"R$ {format_price(unit_info['preco_por_litro'])}/L"
        elif 'preco_por_unidade' in unit_info: # Produtos unitários
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_unidade'])}/unidade"
        elif 'preco_por_embalagem' in unit_info: # Produtos com múltiplas embalagens
             if 'preco_por_100' in unit_info:
                 new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100'])}/100(g/ml)"
             elif 'preco_por_100_base' in unit_info:
                 new_unit_price_str = f"R$ {format_price(unit_info['preco_por_100_base'])}/100(g/ml)"
             else:
                 new_unit_price_str = f"R$ {format_price(unit_info['preco_por_embalagem'])}/embalagem"
        elif 'preco_por_rolo' in unit_info: # Rolos simples
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_rolo'])}/rolo"
        elif 'preco_por_folha' in unit_info:
            new_unit_price_str = f"R$ {format_price(unit_info['preco_por_folha'])}/folha"
        else:
            new_unit_price_str = f"R$ {format_price(new_price)}/unidade"

        # timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S") # Opcional, usar o default now() do banco

        updated_product = {
            "preco": new_price,
            "preco_por_unidade_formatado": new_unit_price_str,
            # "timestamp": timestamp # Considere usar o default now() do banco ou um objeto datetime
        }

        # === ATUALIZANDO NO SUPABASE ===
        # Usar o ID único do produto para atualizar
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
    """Callback para o botão 'Excluir'."""
    query = update.callback_query
    await query.answer()
    # Extrair o ID do produto do callback_data
    product_id = query.data.split("_")[1] # Ex: "delete_12345" -> ["delete", "12345"]
    user_id = query.from_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === BUSCAR PRODUTO PELO ID E GRUPO ===
        # Isso garante que o usuário só delete produtos do próprio grupo
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
            f"📝 *Observações:* {product['observacoes']}\n\n"
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
    """Confirma a exclusão do produto."""
    if update.message.text != "✅ Confirmar":
        return await cancel(update, context)

    product = context.user_data.get('deleting_product')
    if not product:
        await update.message.reply_text("❌ Erro ao confirmar exclusão. Tente novamente.")
        return MAIN_MENU

    user_id = update.effective_user.id
    try:
        # === USANDO A NOVA FUNÇÃO COM SUPABASE ===
        grupo_id = await get_grupo_id(user_id)

        # === VERIFICAR PERMISSÃO E EXCLUIR NO SUPABASE ===
        # Garantir que o produto pertence ao grupo do usuário antes de excluir
        check_response = supabase.table("produtos").select("id").eq("id", product['id']).eq("grupo_id", grupo_id).limit(1).execute()
        if not check_response.data:
            await update.message.reply_text("❌ Você não tem permissão para excluir este produto.")
            return MAIN_MENU

        # === EXCLUINDO NO SUPABASE ===
        # Usar o ID único do produto para deletar
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


# === FLASK + WEBHOOK ===
# Mantém Flask apenas para /, /healthz e manter o Render feliz com um servidor HTTP
# app = Flask(__name__)
# A instância do Application será criada e configurada no start_bot
# bot_application = None # <<<--- JÁ DEFINIDA COMO GLOBAL ACIMA
# bot_event_loop = None # <<<--- JÁ DEFINIDA COMO GLOBAL ACIMA

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.route("/")
def home():
    return "🛒 Bot de Compras está no ar!", 200

# >>>>> FUNÇÃO WEBHOOK CORRIGIDA PARA USAR run_coroutine_threadsafe <<<<<
@app.route("/webhook", methods=["POST"])
def webhook():
    global bot_application, bot_event_loop # <<<--- Acessar globais
    # 1. Verificar se o bot e o loop estão prontos
    if bot_application is None or bot_event_loop is None:
        logging.warning("Bot application ou event loop ainda não está pronto para receber atualizações.")
        return "Service Unavailable", 503 # Service Unavailable

    # 2. Obter os dados JSON da requisição
    json_data = request.get_json()
    if not json_data:
        logging.warning("Requisição POST /webhook sem dados JSON.")
        return "Bad Request", 400 # Bad Request

    # 3. Criar o objeto Update
    update = Update.de_json(json_data, bot_application.bot)
    logging.info(f"Webhook recebido: Update ID {update.update_id}, Tipo: {type(update)}")

    # --- CORREÇÃO CRÍTICA ---
    # 4. Agendar o processamento da atualização no loop de eventos principal
    #    de forma thread-safe. Isso é a forma correta de integrar Flask com asyncio.
    try:
        future = asyncio.run_coroutine_threadsafe(
            bot_application.process_update(update), # <<<--- Usando process_update diretamente
            bot_event_loop # <<<--- Passando o loop global
        )
        # Opcional: Adicionar callback para verificar o resultado (não bloqueante)
        # future.add_done_callback(lambda f: logging.debug(f"Update {update.update_id} scheduled. Result: {f.result() if not f.exception() else f.exception()}"))
        logging.debug(f"Update {update.update_id} agendado para processamento no loop de eventos.")
    except Exception as e:
        logging.error(f"Erro ao agendar atualização no loop de eventos: {e}", exc_info=True)
        return "Internal Server Error", 500 # Internal Server Error
    # --- FIM DA CORREÇÃO CRÍTICA ---

    # 5. Retorna 200 OK imediatamente para o Telegram
    logging.debug("Respondendo 200 OK ao Telegram.")
    return "OK", 200
# >>>>> FIM DA ALTERAÇÃO <<<<<

# === MAIN ===
# Função para rodar o Flask em thread separada
def run_flask():
    # Flask escuta na porta especificada pelo Render
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False) # Debug False em produção

# >>>>> BLOCO PRINCIPAL REESCRITO PARA PYTHON 3.13.4 <<<<<
if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO) # Configurar logging
    logging.info("Iniciando bot com webhook via Flask e Python 3.13.4")

    # 1. Inicia o servidor Flask em uma thread separada
    # Isso libera a thread principal para rodar o loop de eventos asyncio
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True # Permite que o processo termine mesmo se a thread Flask estiver ativa
    flask_thread.start()
    logging.info("Servidor Flask iniciado em thread separada.")

    # 2. Cria e configura o loop de eventos principal
    # Esta é a parte central da Sugestão da Pessoa 2
    loop = asyncio.new_event_loop() # <<<--- Cria o loop
    asyncio.set_event_loop(loop)    # <<<--- Define como o loop padrão da thread

    try:
        # 3. Agenda a tarefa de inicialização do bot no loop
        init_task = loop.create_task(start_bot())

        # 4. Executa a tarefa de inicialização até sua conclusão (bot pronto e webhook setado)
        loop.run_until_complete(init_task)
        logging.info("Bot initialized and webhook set.")

        # 5. Mantém o loop de eventos principal rodando para sempre
        # O loop agora está ativo para:
        # - Processar atualizações agendadas pelo webhook (via run_coroutine_threadsafe)
        # - Executar quaisquer outras tarefas assíncronas que o bot precise
        logging.info("Mantendo o loop de eventos principal ativo com loop.run_forever()...")
        loop.run_forever() # <<<--- Mantém o loop rodando
        # O código abaixo de run_forever só executa se o loop for parado (ex: KeyboardInterrupt)
        logging.info("Loop de eventos encerrado normalmente (run_forever retornou).")

    except KeyboardInterrupt:
        logging.info("Recebido KeyboardInterrupt. Encerrando...")
    except Exception as e:
        logging.critical(f"Erro fatal no bot: {e}", exc_info=True) # Nível CRITICAL para erros fatais
    finally:
        # Tenta encerrar graciosamente (opcional, mas recomendado)
        if bot_application:
            # Cancela todas as tarefas pendentes no loop
            pending = asyncio.all_tasks(loop=loop)
            for task in pending:
                task.cancel()
            # Aguarda o cancelamento
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            # Fecha o loop
            loop.close()
            logging.info("Loop de eventos encerrado.")
        else:
            loop.close() # Fecha o loop mesmo se bot_application for None

    logging.info("Bot encerrado.")
    logging.info("=" * 50)
# >>>>> FIM DO BLOCO PRINCIPAL <<<<<
