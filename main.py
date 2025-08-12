import os
import logging
import asyncio
import gspread
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
from oauth2client.service_account import ServiceAccountCredentials

# === CONFIGURAÇÕES ===
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID = "1ShIhn1IQj8txSUshTJh_ypmzoyvIO40HLNi1ZN28rIo"
ABA_NOME = "Página1" # Aba principal de produtos
ABA_USUARIOS = "Usuarios" # Nova aba para mapear user_id -> grupo_id
CRED_FILE = "/etc/secrets/credentials.json" # Certifique-se de que este caminho está correto no Render

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

# === GOOGLE SHEETS ===
def get_sheet():
    """Obtém o cliente autorizado do Google Sheets."""
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    client = gspread.authorize(creds)
    return client

def get_usuarios_sheet(client):
    """Obtém a worksheet de usuários."""
    return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_USUARIOS)

def get_produtos_sheet(client):
    """Obtém a worksheet de produtos."""
    return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_NOME)

async def get_grupo_id(client, user_id: int) -> str:
    """Obtém o grupo_id de um usuário. Se não existir, cria um novo grupo."""
    try:
        usuarios_sheet = get_usuarios_sheet(client)
        usuarios_rows = usuarios_sheet.get_all_values()

        # Procura o usuário na planilha
        for row in usuarios_rows[1:]: # Ignora cabeçalho
            if len(row) > 0 and row[0] == str(user_id):
                if len(row) > 1:
                    return row[1] # Retorna o grupo_id existente
                else:
                    # Usuário existe mas sem grupo_id (caso antigo), cria um
                    break

        # Se não encontrou ou não tem grupo_id, cria um novo grupo
        novo_grupo_id = str(uuid.uuid4())
        usuarios_sheet.append_row([str(user_id), novo_grupo_id])
        logging.info(f"Novo grupo criado para user_id {user_id}: {novo_grupo_id}")
        return novo_grupo_id

    except Exception as e:
        logging.error(f"Erro ao obter/criar grupo_id para user_id {user_id}: {e}")
        # Fallback: usar o próprio user_id como grupo_id
        return str(user_id)

# === NOVA FUNÇÃO CORRIGIDA: ADICIONAR USUÁRIO AO GRUPO ===
async def adicionar_usuario_ao_grupo(client, novo_user_id: int, codigo_convite: str, convidante_user_id: int = None):
    """Adiciona um novo usuário a um grupo baseado no código de convite (que é o grupo_id)."""
    try:
        usuarios_sheet = get_usuarios_sheet(client)
        usuarios_rows = usuarios_sheet.get_all_values()

        if not usuarios_rows:
            return False, "❌ Erro ao acessar dados de usuários."

        # 1. Verificar se o codigo_convite (grupo_id) existe na planilha
        grupo_id_valido = False
        for row in usuarios_rows[1:]: # Ignora cabeçalho
            if len(row) > 1 and row[1] == codigo_convite:
                grupo_id_valido = True
                grupo_id_para_adicionar = codigo_convite
                break

        if not grupo_id_valido:
            return False, "❌ Código de convite inválido."

        # 2. Verificar se o usuário já está NO MESMO grupo
        for row in usuarios_rows[1:]:
            if len(row) > 0 and row[0] == str(novo_user_id):
                if len(row) > 1 and row[1] == grupo_id_para_adicionar:
                    return True, f"✅ Você já está no grupo '{grupo_id_para_adicionar}'."

        # 3. Se o usuário não está no grupo, adiciona ou atualiza
        # Procura a linha do usuário (para atualizar)
        linha_usuario_existente = None
        for i, row in enumerate(usuarios_rows[1:]):
             if len(row) > 0 and row[0] == str(novo_user_id):
                 linha_usuario_existente = i + 2 # +2 por causa do cabeçalho e índice 1-based
                 break

        if linha_usuario_existente:
            # Atualiza a linha existente com o novo grupo_id
            usuarios_sheet.update_cell(linha_usuario_existente, 1, str(novo_user_id)) # Coluna A: user_id
            usuarios_sheet.update_cell(linha_usuario_existente, 2, grupo_id_para_adicionar) # Coluna B: grupo_id
            logging.info(f"Usuário {novo_user_id} atualizado para o grupo {grupo_id_para_adicionar}")
        else:
            # Adiciona nova linha
            usuarios_sheet.append_row([str(novo_user_id), grupo_id_para_adicionar])
            logging.info(f"Usuário {novo_user_id} adicionado ao grupo {grupo_id_para_adicionar}")

        # Notificar membros do grupo (opcional)
        logging.info(f"Notificação: Novo membro {novo_user_id} entrou no grupo {grupo_id_para_adicionar}. Convidado por {convidante_user_id}")
        return True, f"✅ Você foi adicionado ao grupo '{grupo_id_para_adicionar}'!"

    except Exception as e:
        logging.error(f"Erro ao adicionar usuário {novo_user_id} ao grupo com convite {codigo_convite}: {e}")
        return False, "❌ Erro ao processar o convite. Tente novamente mais tarde."

async def listar_membros_do_grupo(client, grupo_id: str) -> list:
    """Lista os user_ids dos membros de um grupo."""
    try:
        usuarios_sheet = get_usuarios_sheet(client)
        usuarios_rows = usuarios_sheet.get_all_values()[1:] # Ignora cabeçalho
        return [int(row[0]) for row in usuarios_rows if len(row) > 1 and row[1] == grupo_id]
    except Exception as e:
        logging.error(f"Erro ao listar membros do grupo {grupo_id}: {e}")
        return []

# === FUNÇÕES AUXILIARES ===
def format_price(price):
    """Formata float para string com vírgula decimal (para exibição)"""
    return "{:,.2f}".format(price).replace(".", ",")

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
    client = get_sheet()
    grupo_id = await get_grupo_id(client, user_id)
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

    client = get_sheet()
    sucesso, mensagem = await adicionar_usuario_ao_grupo(client, user_id, codigo_convite)

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
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)

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
        'preco': price_str,
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
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id) # Obtém o grupo_id do usuário
        sheet = get_produtos_sheet(client)

        # Formatar a string do preço por unidade para salvar na planilha
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

        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        sheet.append_row([
            grupo_id, # Coluna A: grupo_id
            product['nome'], # Coluna B
            product['tipo'], # Coluna C
            product['marca'], # Coluna D
            product['unidade'], # Coluna E
            product['preco'], # Coluna F
            product['observacoes'], # Coluna G
            unit_price_str, # Coluna H: Preço por Unidade de Medida
            timestamp, # Coluna I
            grupo_id # Coluna J: grupo_id (duplicado conforme solicitado)
        ])
        await update.message.reply_text(
            f"✅ Produto *{product['nome']}* salvo com sucesso na lista do grupo!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Erro ao salvar produto: {e}")
        await update.message.reply_text(
            "❌ Erro ao salvar produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

# === LISTAR PRODUTOS ===
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)
        sheet = get_produtos_sheet(client)
        dados = sheet.get_all_records()

        # Filtra produtos pelo grupo_id do usuário
        produtos_do_grupo = [linha for linha in dados if str(linha.get('grupo_id', '')) == grupo_id]

        if not produtos_do_grupo:
            await update.message.reply_text("📭 Nenhum produto na lista ainda.", reply_markup=main_menu_keyboard())
            return MAIN_MENU

        texto = "📋 *Lista de Produtos do seu Grupo:*\n\n"
        # Mostra os últimos 20 registros
        for linha in produtos_do_grupo[-20:]:
            obs = f" ({linha['Observações']})" if linha['Observações'] else ""
            texto += f"🔹 *{linha['Produto']}* - {linha['Marca']} - {linha['Unidade']} - R${linha['Preço']}{obs}\n"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    except Exception as e:
        logging.error(f"Erro ao listar produtos: {e}")
        await update.message.reply_text("❌ Erro ao acessar a lista.", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# === FLASK + WEBHOOK ===
app = Flask(__name__)
application = None

# === NOVO: Endpoint para Health Check do Render ===
@app.route("/healthz")
def healthz():
    # Você pode adicionar lógica mais complexa aqui se quiser verificar
    # a saúde real do bot (ex: conexão com o Google Sheets)
    # Por enquanto, apenas retorna 200 OK.
    return "OK", 200

@app.route("/")
def home():
    return "🛒 Bot de Compras está no ar!", 200

@app.route("/webhook", methods=["POST"])
async def webhook():
    json_data = request.get_json()
    update = Update.de_json(json_data, application.bot)
    await application.process_update(update)
    return "OK", 200

# === MAIN ===
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

