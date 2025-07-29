import os
import logging
import asyncio
import gspread
import re
from threading import Thread
from datetime import datetime
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from oauth2client.service_account import ServiceAccountCredentials

# === CONFIGURAÇÕES ===
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID = "1ShIhn1IQj8txSUshTJh_ypmzoyvIO40HLNi1ZN28rIo"
ABA_NOME = "Página1"
CRED_FILE = "/etc/secrets/credentials.json" # Certifique-se de que este caminho está correto no Render

# === ESTADOS DO CONVERSATIONHANDLER ===
# Definindo os estados de forma clara e explícita
MAIN_MENU, AWAIT_PRODUCT_DATA, CONFIRM_PRODUCT, AWAIT_EDIT_DELETE_CHOICE, AWAIT_EDIT_PRICE, AWAIT_DELETION_CHOICE, CONFIRM_DELETION, SEARCH_PRODUCT_INPUT = range(8)

# === LOGGING ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# === GOOGLE SHEETS ===
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_NOME)

# === TECLADOS ===
def main_menu_keyboard():
    # Botão "🕒 Histórico" removido conforme solicitado
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Adicionar Produto"), KeyboardButton("✏️ Editar ou Excluir")],
        [KeyboardButton("📋 Listar Produtos"), KeyboardButton("🔍 Pesquisar Produto")],
        [KeyboardButton("ℹ️ Ajuda")]
    ], resize_keyboard=True)

def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Cancelar")]],
        resize_keyboard=True
    )

# === FUNÇÕES AUXILIARES ===
def parse_price(price_str):
    """Converte preço com PONTO para float"""
    try:
        # Assume que o preço já vem com ponto como separador decimal
        return float(price_str)
    except ValueError:
        return None

def format_price(price):
    """Formata float para string com vírgula decimal (para exibição)"""
    return "{:,.2f}".format(price).replace(".", ",")

def calculate_unit_price(unit_str, price):
    """Calcula preço por unidade de medida com base na nova estrutura"""
    unit_str_lower = unit_str.lower().strip()
    try:
        price = float(price)
    except (ValueError, TypeError):
        return {'preco_unitario': price, 'unidade': unit_str}

    # Padrões de regex para identificar unidades
    # Ordem importante: verificar os mais específicos primeiro
    patterns = {
        # Papel Higiênico e similares: "6 rolos, 30M"
        'rolos_e_metros': r'(\d+(?:[.]?\d*))\s*rolos?\s*,\s*(\d+(?:[.]?\d*))\s*m',
        # Múltiplas embalagens: "3 tubos de 90g", "2 pacotes de 500ml"
        'multiplas_embalagens': r'(\d+(?:[.]?\d*))\s*(tubos?|pacotes?|caixas?)\s*de\s*(\d+(?:[.]?\d*))\s*(kg|g|l|ml)',
        # Unidades simples
        'kg': r'(\d+(?:[.]?\d*))\s*kg',
        'g': r'(\d+(?:[.]?\d*))\s*g',
        'l': r'(\d+(?:[.]?\d*))\s*l',
        'ml': r'(\d+(?:[.]?\d*))\s*ml',
        'und': r'(\d+(?:[.]?\d*))\s*(?:und|unid|unidades?)',
        # Rolo simples (depois de verificar rolos_e_metros)
        'rolo_simples': r'(\d+(?:[.]?\d*))\s*rolos?',
        # Metro isolado (depois de verificar rolos_e_metros)
        'metro': r'(\d+(?:[.]?\d*))\s*m',
        # Folhas
        'folhas': r'(\d+(?:[.]?\d*))\s*folhas?',
        # Embalagens simples (depois de verificar multiplas_embalagens)
        'embalagem_simples': r'(\d+(?:[.]?\d*))\s*(pacotes?|caixas?|tubos?)',
    }

    # Verifica cada padrão na ordem correta
    # 1. Roilos e Metros
    if re.search(patterns['rolos_e_metros'], unit_str_lower):
        match = re.search(patterns['rolos_e_metros'], unit_str_lower)
        rolos = float(match.group(1))
        metros = float(match.group(2))
        if rolos > 0 and metros > 0:
            return {
                'preco_por_rolo': price / rolos,
                'preco_por_metro': price / metros,
                'unidade': f"{int(rolos) if rolos.is_integer() else rolos} rolos, {int(metros) if metros.is_integer() else metros}m"
            }

    # 2. Múltiplas Embalagens
    elif re.search(patterns['multiplas_embalagens'], unit_str_lower):
        match = re.search(patterns['multiplas_embalagens'], unit_str_lower)
        qtd = float(match.group(1))
        tipo_embalagem = match.group(2) # tubo, pacote, caixa
        peso_volume = float(match.group(3))
        unidade_medida = match.group(4) # g, ml, kg, l

        if qtd > 0:
            preco_por_unidade = price / qtd
            # Converter para unidade base para cálculo de preço por 100g/ml ou por kg/l
            if unidade_medida == 'g':
                total_g = qtd * peso_volume
                preco_por_100g = price / total_g * 100 if total_g > 0 else 0
                return {
                    'preco_por_unidade': preco_por_unidade,
                    'preco_por_100g': preco_por_100g,
                    'unidade': f"{int(qtd) if qtd.is_integer() else qtd} {tipo_embalagem} de {int(peso_volume) if peso_volume.is_integer() else peso_volume}g"
                }
            elif unidade_medida == 'kg':
                 total_kg = qtd * peso_volume
                 preco_por_kg = price / total_kg if total_kg > 0 else 0
                 return {
                     'preco_por_unidade': preco_por_unidade,
                     'preco_por_kg': preco_por_kg,
                     'unidade': f"{int(qtd) if qtd.is_integer() else qtd} {tipo_embalagem} de {int(peso_volume) if peso_volume.is_integer() else peso_volume}kg"
                 }
            elif unidade_medida == 'ml':
                total_ml = qtd * peso_volume
                preco_por_100ml = price / total_ml * 100 if total_ml > 0 else 0
                return {
                    'preco_por_unidade': preco_por_unidade,
                    'preco_por_100ml': preco_por_100ml,
                    'unidade': f"{int(qtd) if qtd.is_integer() else qtd} {tipo_embalagem} de {int(peso_volume) if peso_volume.is_integer() else peso_volume}ml"
                }
            elif unidade_medida == 'l':
                total_l = qtd * peso_volume
                preco_por_litro = price / total_l if total_l > 0 else 0
                return {
                    'preco_por_unidade': preco_por_unidade,
                    'preco_por_litro': preco_por_litro,
                    'unidade': f"{int(qtd) if qtd.is_integer() else qtd} {tipo_embalagem} de {int(peso_volume) if peso_volume.is_integer() else peso_volume}L"
                }

    # 3. Unidades Simples
    elif re.search(patterns['kg'], unit_str_lower):
        match = re.search(patterns['kg'], unit_str_lower)
        kg = float(match.group(1))
        if kg > 0:
            return {'preco_por_kg': price / kg, 'unidade': f"{int(kg) if kg.is_integer() else kg} kg"}

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
            return {'preco_por_litro': price / l, 'unidade': f"{int(l) if l.is_integer() else l}L"}

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

    # 4. Rolo Simples
    elif re.search(patterns['rolo_simples'], unit_str_lower):
        match = re.search(patterns['rolo_simples'], unit_str_lower)
        rolos = float(match.group(1))
        if rolos > 0:
            return {'preco_por_rolo': price / rolos, 'unidade': f"{int(rolos) if rolos.is_integer() else rolos} rolos"}

    # 5. Metro Isolado
    elif re.search(patterns['metro'], unit_str_lower):
        match = re.search(patterns['metro'], unit_str_lower)
        metros = float(match.group(1))
        if metros > 0:
            return {'preco_por_metro': price / metros, 'unidade': f"{int(metros) if metros.is_integer() else metros}m"}

    # 6. Folhas
    elif re.search(patterns['folhas'], unit_str_lower):
        match = re.search(patterns['folhas'], unit_str_lower)
        folhas = float(match.group(1))
        if folhas > 0:
            return {'preco_por_folha': price / folhas, 'unidade': f"{int(folhas) if folhas.is_integer() else folhas} folhas"}

    # 7. Embalagem Simples (pacote, caixa, tubo)
    elif re.search(patterns['embalagem_simples'], unit_str_lower):
        match = re.search(patterns['embalagem_simples'], unit_str_lower)
        qtd = float(match.group(1))
        tipo_embalagem = match.group(2) # pacote, caixa, tubo
        if qtd > 0:
            return {'preco_por_unidade': price / qtd, 'unidade': f"{int(qtd) if qtd.is_integer() else qtd} {tipo_embalagem}"}

    # Se nenhum padrão for encontrado, retorna o preço unitário com a unidade original
    return {'preco_unitario': price, 'unidade': unit_str}

# === FUNÇÕES DE PESQUISA ===
async def show_search_results_direct(update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
    """Mostra os resultados da pesquisa direta de produto."""
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()[1:] # Ignora cabeçalho
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha para pesquisa direta: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    # Filtra produtos cujo nome começa com o termo pesquisado
    matching_rows = [row for row in rows if row[0].lower().startswith(search_term.lower())]

    if not matching_rows:
        await update.message.reply_text(
            f"📭 Produto '*{search_term}*' não encontrado.\n\n"
            "Você pode adicioná-lo usando o botão *➕ Adicionar Produto*.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    message = f"🔍 Resultados para '*{search_term}*':\n\n"
    produtos_agrupados = {}
    for row in matching_rows:
         nome = row[0]
         if nome not in produtos_agrupados:
             produtos_agrupados[nome] = []
         produtos_agrupados[nome].append(row)

    for nome, registros in produtos_agrupados.items():
        message += f"🏷️ *{nome}*\n"
        for registro in registros:
            # Ajusta para a nova estrutura da planilha (8 colunas)
            tipo = registro[1] if len(registro) > 1 else "N/A"
            marca = registro[2] if len(registro) > 2 else "N/A"
            unidade = registro[3] if len(registro) > 3 else "N/A"
            preco = registro[4] if len(registro) > 4 else "N/A"
            obs = registro[5] if len(registro) > 5 else ""
            preco_por_unidade = registro[6] if len(registro) > 6 else "N/A"
            timestamp = registro[7] if len(registro) > 7 else "N/A"

            message += f"  📦 {tipo} | 🏭 {marca}\n"
            message += f"  📏 {unidade} | 💵 R$ {preco}\n" # Exibe com ponto
            if preco_por_unidade and preco_por_unidade != "N/A":
                message += f"  📊 {preco_por_unidade}\n"
            if obs:
                message += f"  📝 {obs}\n"
            message += f"  🕒 {timestamp}\n---\n"

    # Envia a mensagem em partes se for muito longa
    if len(message) > 4096:
        parts = [message[i:i+4096] for i in range(0, len(message), 4096)]
        for part in parts:
            await update.message.reply_text(part, reply_markup=main_menu_keyboard() if part is parts[-1] else None, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            message,
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    return MAIN_MENU

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Bot de Compras Inteligente* 🛒\nEscolha uma opção ou digite o nome de um produto para pesquisar:",
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

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Mensagem de ajuda atualizada para refletir o uso de ponto no preço
    help_text = (
        "🛒 Como adicionar um produto corretamente:\n"
        "Use o seguinte formato (uma linha por produto):\n"
        "*Produto | Tipo | Marca | Unidade | Preço | Observações*\n\n"
        "*Exemplos:*\n"
        "Arroz, Branco, Camil, 5 kg, 25.99\n" # Ponto no preço
        "Leite, Integral, Italac, 1 L, 4.49\n"  # Ponto no preço
        "Papel Higiênico, Compacto, Max, 12 rolos, 30M, 14.90\n" # Ponto no preço
        "Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n" # Ponto no preço
        "Ovo, Branco, Grande, 30 und, 16.90\n\n" # Ponto no preço
        "*💡 Dicas:*\n"
        "- Use **ponto como separador decimal** no preço (Ex: 4.99).\n" # Instrução atualizada
        "- Para produtos com unidades compostas (como '6 rolos, 40M'), descreva assim para que o sistema calcule o custo por metro.\n"
        "- O sistema automaticamente calculará o **preço por unidade de medida** (Kg, L, ml, g, und, metro, folha, etc.) e informará qual opção é mais econômica.\n"
        "- Você também pode digitar diretamente o nome de um produto para pesquisar seu preço!"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def ask_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Instrução atualizada para refletir o uso de ponto no preço
    await update.message.reply_text(
        "📝 Digite os dados do produto no formato:\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n\n"
        "*Exemplo:* Arroz, Branco, Camil, 5 kg, 25.99\n" # Ponto no preço
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
            "*Exemplo:* Arroz, Branco, Camil, 5 kg, 25.99", # Ponto no preço
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown"
        )
        return AWAIT_PRODUCT_DATA

    # Validação do preço (agora com ponto)
    price_str = data[4].strip()
    price = parse_price(price_str)
    if price is None:
        await update.message.reply_text(
            "⚠️ Preço inválido. Use **ponto como separador decimal** (ex: 4.99).\n" # Mensagem atualizada
            "Por favor, digite novamente os dados do produto:",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown" # Para negrito
        )
        return AWAIT_PRODUCT_DATA

    # Prepara os dados
    product = {
        'nome': data[0].title(),
        'tipo': data[1].title(),
        'marca': data[2].title(),
        'unidade': data[3].strip(),
        'preco': price_str, # Mantém a string original com ponto
        'observacoes': data[5].strip() if len(data) > 5 else ""
    }

    # Calcula preço por unidade
    unit_info = calculate_unit_price(product['unidade'], price)

    # Mostra confirmação
    message = (
        f"🔍 Confirme os dados do produto:\n"
        f"🏷️ *Produto*: {product['nome']}\n"
        f"📦 *Tipo*: {product['tipo']}\n"
        f"🏭 *Marca*: {product['marca']}\n"
        f"📏 *Unidade*: {product['unidade']}\n"
        f"💵 *Preço*: R$ {product['preco']}\n" # Exibe com ponto
    )
    if product['observacoes']:
        message += f"📝 *Observações*: {product['observacoes']}\n"

    # Adiciona cálculos de preço por unidade à mensagem de confirmação
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
    if 'preco_por_rolo' in unit_info:
        message += f"📊 *Preço por rolo*: R$ {format_price(unit_info['preco_por_rolo'])}\n"
    if 'preco_por_metro' in unit_info:
        message += f"📊 *Preço por metro*: R$ {format_price(unit_info['preco_por_metro'])}\n"
    if 'preco_por_folha' in unit_info:
        message += f"📊 *Preço por folha*: R$ {format_price(unit_info['preco_por_folha'])}\n"

    message += "\nDigite ✅ *Confirmar* para salvar ou ❌ *Cancelar* para corrigir"
    context.user_data['current_product'] = product
    context.user_data['unit_info'] = unit_info
    await update.message.reply_text(
        message,
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("✅ Confirmar"), KeyboardButton("❌ Cancelar")]
        ], resize_keyboard=True),
        parse_mode="Markdown"
    )
    return CONFIRM_PRODUCT

async def save_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    product = context.user_data.get('current_product')
    unit_info = context.user_data.get('unit_info')

    if not product or not unit_info:
         await update.message.reply_text(
            "❌ Erro ao salvar produto. Dados não encontrados.",
            reply_markup=main_menu_keyboard()
        )
         return MAIN_MENU

    # Prepara o preço por unidade para salvar na coluna G
    unit_price_str = ""
    # Prioriza mostrar a unidade mais relevante para comparação
    if 'preco_por_kg' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_kg'])}/kg"
    elif 'preco_por_100g' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_100g'])}/100g"
    elif 'preco_por_litro' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_litro'])}/L"
    elif 'preco_por_100ml' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_100ml'])}/100ml"
    elif 'preco_por_unidade' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_unidade'])}/unidade"
    elif 'preco_por_rolo' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_rolo'])}/rolo"
    elif 'preco_por_metro' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_metro'])}/metro"
    elif 'preco_por_folha' in unit_info:
        unit_price_str = f"R$ {format_price(unit_info['preco_por_folha'])}/folha"
    else:
        # Caso fallback, mostra o preço unitário mesmo
        unit_price_str = f"R$ {format_price(parse_price(product['preco']))}/unidade"

    # Salva na planilha
    try:
        sheet = get_sheet()
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        sheet.append_row([
            product['nome'],
            product['tipo'],
            product['marca'],
            product['unidade'],
            product['preco'], # Mantém o formato original com ponto
            product['observacoes'],
            unit_price_str, # Coluna G: Preço por Unidade de Medida
            timestamp
        ])
        await update.message.reply_text(
            f"✅ Produto *{product['nome']}* salvo com sucesso!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Erro ao salvar produto: {e}")
        await update.message.reply_text(
            "❌ Erro ao salvar produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    # Limpa os dados temporários
    context.user_data.clear()
    return MAIN_MENU

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()[1:]  # Ignora cabeçalho
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    if not rows:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    message = "📋 *Lista de Produtos*\n"
    for row in rows:
        # Ajusta para a nova estrutura da planilha (8 colunas)
        nome = row[0] if len(row) > 0 else "N/A"
        tipo = row[1] if len(row) > 1 else "N/A"
        marca = row[2] if len(row) > 2 else "N/A"
        unidade = row[3] if len(row) > 3 else "N/A"
        preco = row[4] if len(row) > 4 else "N/A"
        # obs = row[5] if len(row) > 5 else "" # Não mostrado na lista para economizar espaço
        preco_por_unidade = row[6] if len(row) > 6 else "N/A"

        message += (
            f"🏷️ *{nome}* ({tipo})\n"
            f"🏭 {marca} | 📏 {unidade} | 💵 R$ {preco}\n" # Exibe com ponto
            f"📊 {preco_por_unidade}\n\n" # Mostra o preço calculado
        )

    # Envia a mensagem em partes se for muito longa
    if len(message) > 4096:
        parts = [message[i:i+4096] for i in range(0, len(message), 4096)]
        for part in parts:
            await update.message.reply_text(part, reply_markup=main_menu_keyboard() if part is parts[-1] else None, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            message,
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    return MAIN_MENU

# === NOVA FUNÇÃO: EDITAR OU EXCLUIR ===
async def edit_or_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite selecionar um produto para editar ou excluir."""
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()[1:]
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha para editar/excluir: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    produtos = list(set([row[0] for row in rows])) # Nomes únicos
    if not produtos:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado para editar ou excluir.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    buttons = [[KeyboardButton(name)] for name in sorted(produtos)]
    buttons.append([KeyboardButton("❌ Cancelar")])
    await update.message.reply_text(
        "✏️ Selecione o produto para editar ou excluir:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    )
    return AWAIT_DELETION_CHOICE

async def confirm_edit_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra as opções de editar ou excluir após selecionar o produto."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    produto_nome = update.message.text.strip()
    try:
        sheet = get_sheet()
        all_rows = sheet.get_all_values()
        rows = all_rows[1:] # Ignora cabeçalho
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha para confirmar editar/excluir: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    # Encontra todas as linhas com o produto (índices 1-based para gspread e armazenamento interno)
    matching_entries = []
    for i, row in enumerate(rows):
        if row[0] == produto_nome:
            # Armazena o índice da planilha (1-based) e os dados da linha
            matching_entries.append({
                'sheet_index': i + 2, # +2 porque 'rows' é [1:] e gspread é 1-based
                'data': row
            })

    if not matching_entries:
        await update.message.reply_text(
            f"ℹ️ Produto '{produto_nome}' não encontrado.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    context.user_data['selected_product_name'] = produto_nome
    context.user_data['matching_entries'] = matching_entries

    # Se houver apenas uma entrada, mostra diretamente as opções
    if len(matching_entries) == 1:
        entry = matching_entries[0]
        row_data = entry['data']
        # Formata a mensagem com os detalhes do produto
        message = f"✏️ Produto selecionado:\n"
        message += f"🏷️ *{row_data[0]}* ({row_data[1]})\n"
        message += f"🏭 {row_data[2]}\n"
        message += f"📏 {row_data[3]}\n"
        message += f"💵 *Preço*: R$ {row_data[4]}\n" # Exibe com ponto
        if len(row_data) > 5 and row_data[5]:
            message += f"📝 {row_data[5]}\n"
        if len(row_data) > 6 and row_data[6]:
            message += f"📊 {row_data[6]}\n"
        if len(row_data) > 7 and row_data[7]:
            message += f"🕒 {row_data[7]}\n"

        context.user_data['selected_entry_index'] = entry['sheet_index']
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✏️ Editar Preço"), KeyboardButton("🗑️ Excluir")],
                [KeyboardButton("❌ Cancelar")]
            ], resize_keyboard=True)
        )
        return AWAIT_EDIT_DELETE_CHOICE

    # Se houver múltiplas entradas, lista para o usuário escolher
    else:
        message = f"🔍 Foram encontradas múltiplas entradas para *{produto_nome}*:\n\n"
        for i, entry in enumerate(matching_entries):
            row_data = entry['data']
            # Criamos um identificador único baseado em Tipo, Marca, Unidade para ajudar o usuário a escolher
            tipo = row_data[1] if len(row_data) > 1 else "N/A"
            marca = row_data[2] if len(row_data) > 2 else "N/A"
            unidade = row_data[3] if len(row_data) > 3 else "N/A"
            preco = row_data[4] if len(row_data) > 4 else "N/A"
            
            message += f"{i+1}. {tipo} | {marca} | {unidade} | R$ {preco}\n"
        
        message += "\nDigite o *número* da entrada que deseja editar/excluir:"
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_DELETION_CHOICE # Reutilizando para pegar o número

async def handle_multiple_entry_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lida com a escolha do usuário quando há múltiplas entradas."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    
    try:
        choice = int(update.message.text.strip())
        matching_entries = context.user_data.get('matching_entries', [])
        if 1 <= choice <= len(matching_entries):
            selected_entry = matching_entries[choice - 1] # -1 porque o usuário digita 1-based
            row_data = selected_entry['data']
            
            # Mostra os detalhes da entrada selecionada
            message = f"✏️ Entrada selecionada:\n"
            message += f"🏷️ *{row_data[0]}* ({row_data[1]})\n"
            message += f"🏭 {row_data[2]}\n"
            message += f"📏 {row_data[3]}\n"
            message += f"💵 *Preço*: R$ {row_data[4]}\n" # Exibe com ponto
            if len(row_data) > 5 and row_data[5]:
                message += f"📝 {row_data[5]}\n"
            if len(row_data) > 6 and row_data[6]:
                message += f"📊 {row_data[6]}\n"
            if len(row_data) > 7 and row_data[7]:
                message += f"🕒 {row_data[7]}\n"
                
            context.user_data['selected_entry_index'] = selected_entry['sheet_index']
            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([
                    [KeyboardButton("✏️ Editar Preço"), KeyboardButton("🗑️ Excluir")],
                    [KeyboardButton("❌ Cancelar")]
                ], resize_keyboard=True)
            )
            return AWAIT_EDIT_DELETE_CHOICE
        else:
            raise ValueError("Escolha inválida")
    except (ValueError, IndexError):
        await update.message.reply_text(
            "⚠️ Escolha inválida. Por favor, digite o número da entrada listada:",
            reply_markup=cancel_keyboard()
        )
        return AWAIT_DELETION_CHOICE # Volta para pedir a escolha novamente

async def process_edit_delete_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa a escolha de editar ou excluir."""
    text = update.message.text.strip()
    if text == "❌ Cancelar":
        return await cancel(update, context)
    elif text == "✏️ Editar Preço":
        await update.message.reply_text(
            "Digite o *novo preço* (use ponto como separador decimal, ex: 4.99):",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown"
        )
        return AWAIT_EDIT_PRICE
    elif text == "🗑️ Excluir":
        produto_nome = context.user_data.get('selected_product_name')
        sheet_index = context.user_data.get('selected_entry_index')
        
        if not produto_nome or not sheet_index:
            await update.message.reply_text(
                "❌ Erro ao identificar o produto para exclusão.",
                reply_markup=main_menu_keyboard()
            )
            return MAIN_MENU
            
        context.user_data['product_to_delete_index'] = sheet_index
        await update.message.reply_text(
            f"⚠️ Confirmar exclusão de *{produto_nome}*?\n"
            "Digite '✅ SIM' para confirmar ou '❌ NÃO' para cancelar",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✅ SIM"), KeyboardButton("❌ NÃO")]
            ], resize_keyboard=True)
        )
        return CONFIRM_DELETION
    else:
        await update.message.reply_text(
            "Opção inválida. Escolha '✏️ Editar Preço' ou '🗑️ Excluir'.",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✏️ Editar Preço"), KeyboardButton("🗑️ Excluir")],
                [KeyboardButton("❌ Cancelar")]
            ], resize_keyboard=True)
        )
        return AWAIT_EDIT_DELETE_CHOICE

async def handle_edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lida com a entrada do novo preço para edição."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    new_price_str = update.message.text.strip()
    new_price = parse_price(new_price_str)
    
    if new_price is None:
        await update.message.reply_text(
            "⚠️ Preço inválido. Use **ponto como separador decimal** (ex: 4.99).\n"
            "Por favor, digite o novo preço:",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown"
        )
        return AWAIT_EDIT_PRICE

    produto_nome = context.user_data.get('selected_product_name')
    sheet_index = context.user_data.get('selected_entry_index')
    
    if not produto_nome or not sheet_index:
        await update.message.reply_text(
            "❌ Erro ao identificar o produto para edição.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    try:
        sheet = get_sheet()
        # Busca a linha novamente para garantir que não mudou
        all_rows = sheet.get_all_values()
        if sheet_index > len(all_rows):
             raise IndexError("Linha não encontrada")
             
        row_data = all_rows[sheet_index - 1] # -1 para 0-based index do Python
        
        # Atualiza o preço na coluna E (índice 5 no gspread é a coluna 5, mas update_cell usa 1-based)
        sheet.update_cell(sheet_index, 5, new_price_str) # Coluna E é o índice 5 no gspread
        
        # Recalcula o preço por unidade (opcional, mas recomendado)
        unidade_str = row_data[3] if len(row_data) > 3 else ""
        unit_info = calculate_unit_price(unidade_str, new_price)
        
        # Prepara o novo preço por unidade para salvar na coluna G
        unit_price_str = ""
        if 'preco_por_kg' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_kg'])}/kg"
        elif 'preco_por_100g' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_100g'])}/100g"
        elif 'preco_por_litro' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_litro'])}/L"
        elif 'preco_por_100ml' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_100ml'])}/100ml"
        elif 'preco_por_unidade' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_unidade'])}/unidade"
        elif 'preco_por_rolo' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_rolo'])}/rolo"
        elif 'preco_por_metro' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_metro'])}/metro"
        elif 'preco_por_folha' in unit_info:
            unit_price_str = f"R$ {format_price(unit_info['preco_por_folha'])}/folha"
        else:
            unit_price_str = f"R$ {format_price(new_price)}/unidade"
            
        # Atualiza o preço por unidade na coluna G (índice 7 no gspread)
        sheet.update_cell(sheet_index, 7, unit_price_str) # Coluna G é o índice 7 no gspread
        
        # Atualiza o timestamp na coluna H (índice 8 no gspread)
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        sheet.update_cell(sheet_index, 8, timestamp) # Coluna H é o índice 8 no gspread
        
        await update.message.reply_text(
            f"✅ Preço do produto *{produto_nome}* atualizado para R$ {new_price_str}!",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Erro ao editar preço do produto: {e}")
        await update.message.reply_text(
            "❌ Erro ao editar preço do produto. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    # Limpa os dados temporários
    context.user_data.clear()
    return MAIN_MENU

async def execute_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executa a exclusão do produto."""
    text = update.message.text.upper().strip()
    if text == "✅ SIM" or text == "SIM":
        sheet_index = context.user_data.get('product_to_delete_index')

        if not sheet_index:
             await update.message.reply_text(
                "❌ Erro ao excluir produto. Dados não encontrados.",
                reply_markup=main_menu_keyboard()
            )
             return MAIN_MENU

        try:
            sheet = get_sheet()
            sheet.delete_rows(sheet_index)
            await update.message.reply_text(
                f"🗑️ Produto foi excluído permanentemente.",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Erro ao excluir produto: {e}")
            await update.message.reply_text(
                "❌ Erro ao excluir produto. Tente novamente mais tarde.",
                reply_markup=main_menu_keyboard()
            )
    else:
        await update.message.reply_text(
            "❌ Exclusão cancelada.",
            reply_markup=main_menu_keyboard()
        )
    # Limpa os dados temporários
    context.user_data.clear()
    return MAIN_MENU

# === FUNÇÃO DE PESQUISA ===
async def search_product_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite ao usuário digitar o nome de um produto para ver seu histórico."""
    await update.message.reply_text(
        "🔍 Digite o *nome* (ou parte inicial do nome) do produto para pesquisar:",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown"
    )
    return SEARCH_PRODUCT_INPUT

async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra os resultados da pesquisa de produto."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    search_term = update.message.text.strip().title()
    return await show_search_results_direct(update, context, search_term)

# === HANDLER PARA PESQUISA DIRETA ===
async def handle_direct_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lida com a pesquisa direta de produtos no menu principal ou na tela de pesquisa."""
    search_term = update.message.text.strip()
    
    # Verifica se o termo corresponde a algum botão do menu principal
    menu_buttons = ["➕ Adicionar Produto", "✏️ Editar ou Excluir", "📋 Listar Produtos", "🔍 Pesquisar Produto", "ℹ️ Ajuda", "❌ Cancelar"]
    if search_term in menu_buttons:
        # Se for um botão do menu, ignora a pesquisa direta
        return MAIN_MENU
    
    # Realiza a pesquisa direta
    return await show_search_results_direct(update, context, search_term)

# === CONVERSATION HANDLER ===
def build_conv_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_data),
            MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
            MessageHandler(filters.Regex("^✏️ Editar ou Excluir$"), edit_or_delete_product),
            MessageHandler(filters.Regex("^🔍 Pesquisar Produto$"), search_product_history),
            MessageHandler(filters.Regex("^ℹ️ Ajuda$"), show_help),
            # Handler para pesquisa direta (qualquer texto que não seja um comando do menu)
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct_search)
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_data),
                MessageHandler(filters.Regex("^✏️ Editar ou Excluir$"), edit_or_delete_product),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^🔍 Pesquisar Produto$"), search_product_history),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), show_help),
                # Handler para pesquisa direta no estado MAIN_MENU
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct_search)
            ],
            AWAIT_PRODUCT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_data)],
            CONFIRM_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_product)],
            # Estados para a nova funcionalidade
            AWAIT_DELETION_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_edit_delete), # Primeira escolha de produto
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_multiple_entry_choice) # Escolha entre múltiplas entradas
            ],
            AWAIT_EDIT_DELETE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_delete_choice)],
            AWAIT_EDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_price)],
            CONFIRM_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_deletion)],
            SEARCH_PRODUCT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, show_search_results)], # Estado adicionado
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancelar$"), cancel)],
    )

# === FLASK + WEBHOOK SETUP ===
app = Flask(__name__)
application = None
loop = None

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if not application or not loop:
            logging.error("Application ou event loop não inicializados!")
            return "Loop não iniciado", 500
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)
        loop.call_soon_threadsafe(asyncio.create_task, application.process_update(update))
        return "OK", 200
    except Exception as e:
        logging.error(f"Erro no webhook: {e}")
        return "Erro", 500

@app.route("/")
def home():
    return "🤖 Bot de Lista de Compras está no ar!", 200

@app.route("/healthz")
def health_check():
    return "OK", 200

async def start_bot():
    global application
    application = (
        Application.builder()
        .token(TOKEN)
        .concurrent_updates(True)
        .build()
    )
    application.add_handler(build_conv_handler())
    await application.initialize()
    webhook_url = f"{os.environ['RENDER_EXTERNAL_URL']}/webhook"
    await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logging.info(f"Webhook configurado: {webhook_url}")
    await application.start()
    while True:
        await asyncio.sleep(3600) # Mantém o bot rodando

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    # Garantir que os estados estejam definidos corretamente no escopo global
    # (Já estão definidos no topo, mas reforçando)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    Thread(target=run_flask).start()
    try:
        loop.run_until_complete(start_bot())
    except KeyboardInterrupt:
        logging.info("Bot encerrado.")
    except Exception as e:
        logging.error(f"Erro fatal: {e}")
    finally:
        loop.close()
