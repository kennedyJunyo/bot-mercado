import os
import logging
import asyncio
import gspread
import re
import uuid # Para gerar códigos de convite únicos
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
    filters
    CallbackQueryHandler
)
from oauth2client.service_account import ServiceAccountCredentials

# === CONFIGURAÇÕES ===
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
SPREADSHEET_ID = "1ShIhn1IQj8txSUshTJh_ypmzoyvIO40HLNi1ZN28rIo"
ABA_NOME = "Página1" # Aba principal de produtos
ABA_USUARIOS = "Usuarios" # Nova aba para mapear user_id -> grupo_id
CRED_FILE = "/etc/secrets/credentials.json" # Certifique-se de que este caminho está correto no Render

# === ESTADOS DO CONVERSATIONHANDLER ===
# Definindo os estados de forma clara e explícita
# Adicionando estados para o fluxo de compartilhamento
MAIN_MENU, AWAIT_PRODUCT_DATA, CONFIRM_PRODUCT, AWAIT_EDIT_DELETE_CHOICE, AWAIT_EDIT_PRICE, AWAIT_DELETION_CHOICE, CONFIRM_DELETION, SEARCH_PRODUCT_INPUT, AWAIT_ENTRY_CHOICE, AWAIT_INVITE_CODE = range(10)

# === LOGGING ===
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# === CONSTANTES PARA COLUNAS ===
# Índices baseados em 0 (Python) para facilitar o acesso às colunas
COL_USER_GRUPO_ID = 0  # Coluna A (índice 0) na planilha de produtos
COL_PRODUTO = 1       # Coluna B
COL_TIPO = 2          # Coluna C
COL_MARCA = 3         # Coluna D
COL_UNIDADE = 4       # Coluna E
COL_PRECO = 5         # Coluna F
COL_OBS = 6           # Coluna G
COL_PRECO_UNID = 7    # Coluna H
COL_TIMESTAMP = 8     # Coluna I
COL_GRUPO_ID_LEGADO = 9 # Coluna J - Para armazenar o grupo_id também

# === GOOGLE SHEETS ===
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    client = gspread.authorize(creds)
    return client

def get_produtos_sheet(client):
    return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_NOME)

def get_usuarios_sheet(client):
    try:
        return client.open_by_key(SPREADSHEET_ID).worksheet(ABA_USUARIOS)
    except gspread.exceptions.WorksheetNotFound:
        # Se a aba não existir, cria-a
        sheet = client.open_by_key(SPREADSHEET_ID).add_worksheet(title=ABA_USUARIOS, rows="100", cols="2")
        sheet.append_row(["user_id", "grupo_id"]) # Cabeçalhos
        return sheet

# === FUNÇÕES DE GRUPO ===
async def get_grupo_id(client, user_id: int) -> str:
    """Obtém o grupo_id associado a um user_id. Cria um novo grupo se necessário."""
    try:
        usuarios_sheet = get_usuarios_sheet(client)
        usuarios_rows = usuarios_sheet.get_all_values()
        
        if not usuarios_rows:
             # Se a aba estiver vazia, cria o cabeçalho
             usuarios_sheet.append_row(["user_id", "grupo_id"])
             usuarios_rows = [["user_id", "grupo_id"]]

        # Procura o user_id
        for row in usuarios_rows[1:]: # Ignora cabeçalho
            if len(row) > 0 and row[0] == str(user_id):
                if len(row) > 1:
                    return row[1] # Retorna o grupo_id existente
                else:
                    # Caso a linha exista mas não tenha grupo_id (corrupção?)
                    break

        # Se não encontrou, cria um novo grupo para este usuário
        novo_grupo_id = f"grupo_{uuid.uuid4().hex[:8]}"
        usuarios_sheet.append_row([str(user_id), novo_grupo_id])
        logging.info(f"Novo grupo criado para user_id {user_id}: {novo_grupo_id}")
        return novo_grupo_id

    except Exception as e:
        logging.error(f"Erro ao obter/criar grupo_id para user_id {user_id}: {e}")
        # Fallback: usar o próprio user_id como grupo_id
        return str(user_id)

async def adicionar_usuario_ao_grupo(client, novo_user_id: int, codigo_convite: str, convidante_user_id: int = None):
    """Adiciona um novo usuário a um grupo baseado no código de convite."""
    try:
        usuarios_sheet = get_usuarios_sheet(client)
        usuarios_rows = usuarios_sheet.get_all_values()
        
        if not usuarios_rows:
            return False, "Erro ao acessar dados de usuários."

        # Procura o código de convite (que é o grupo_id)
        grupo_id_para_adicionar = None
        for row in usuarios_rows[1:]: # Ignora cabeçalho
            if len(row) > 1 and row[1] == codigo_convite:
                grupo_id_para_adicionar = codigo_convite
                break
        
        if not grupo_id_para_adicionar:
            return False, "Código de convite inválido."

        # Verifica se o usuário já está no grupo
        for row in usuarios_rows[1:]:
            if len(row) > 0 and row[0] == str(novo_user_id):
                 if len(row) > 1 and row[1] == grupo_id_para_adicionar:
                     return True, f"Você já está no grupo '{grupo_id_para_adicionar}'."
                 else:
                     # Usuário existe mas em outro grupo. Atualiza.
                     # (Ou você pode decidir se permite múltiplos grupos por usuário)
                     # Por enquanto, vamos atualizar para o novo grupo
                     pass

        # Adiciona ou atualiza o usuário no grupo
        # Verifica se o usuário já existe na planilha (para atualizar)
        usuario_atualizado = False
        for i, row in enumerate(usuarios_rows[1:]):
            if len(row) > 0 and row[0] == str(novo_user_id):
                # Atualiza a linha existente
                usuarios_sheet.update_cell(i + 2, 1, str(novo_user_id)) # +2 por causa do cabeçalho e índice 1-based do gspread
                usuarios_sheet.update_cell(i + 2, 2, grupo_id_para_adicionar)
                usuario_atualizado = True
                break
        
        if not usuario_atualizado:
            # Adiciona nova linha
            usuarios_sheet.append_row([str(novo_user_id), grupo_id_para_adicionar])
        
        logging.info(f"Usuário {novo_user_id} adicionado/atualizado ao grupo {grupo_id_para_adicionar}")
        
        # Notificar membros do grupo (opcional)
        # Esta parte pode ser expandida para enviar mensagens reais
        # Por enquanto, vamos logar a notificação
        logging.info(f"Notificação: Novo membro {novo_user_id} entrou no grupo {grupo_id_para_adicionar}. Convidado por {convidante_user_id}")
        
        return True, f"✅ Você foi adicionado ao grupo '{grupo_id_para_adicionar}'!"

    except Exception as e:
        logging.error(f"Erro ao adicionar usuário {novo_user_id} ao grupo com convite {codigo_convite}: {e}")
        return False, "Erro ao processar o convite. Tente novamente mais tarde."

async def listar_membros_do_grupo(client, grupo_id: str) -> list:
    """Lista os user_ids dos membros de um grupo."""
    try:
        usuarios_sheet = get_usuarios_sheet(client)
        usuarios_rows = usuarios_sheet.get_all_values()[1:] # Ignora cabeçalho
        return [int(row[0]) for row in usuarios_rows if len(row) > 1 and row[1] == grupo_id]
    except Exception as e:
        logging.error(f"Erro ao listar membros do grupo {grupo_id}: {e}")
        return []

# === TECLADOS ===
def main_menu_keyboard():
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

    patterns = {
        'rolos_e_metros': r'(\d+(?:[.]?\d*))\s*rolos?\s+(\d+(?:[.]?\d*))\s*m',
        'multiplas_embalagens': r'(\d+(?:[.]?\d*))\s*(tubos?|pacotes?|caixas?)\s*de\s*(\d+(?:[.]?\d*))\s*(kg|g|l|ml)',
        'kg': r'(\d+(?:[.]?\d*))\s*kg',
        'g': r'(\d+(?:[.]?\d*))\s*g',
        'l': r'(\d+(?:[.]?\d*))\s*l',
        'ml': r'(\d+(?:[.]?\d*))\s*ml',
        'und': r'(\d+(?:[.]?\d*))\s*(?:und|unid|unidades?)',
        'rolo_simples': r'(\d+(?:[.]?\d*))\s*rolos?',
        'metro': r'(\d+(?:[.]?\d*))\s*m',
        'folhas': r'(\d+(?:[.]?\d*))\s*folhas?',
        'embalagem_simples': r'(\d+(?:[.]?\d*))\s*(pacotes?|caixas?|tubos?)',
    }

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

    elif re.search(patterns['multiplas_embalagens'], unit_str_lower):
        match = re.search(patterns['multiplas_embalagens'], unit_str_lower)
        qtd = float(match.group(1))
        tipo_embalagem = match.group(2)
        peso_volume = float(match.group(3))
        unidade_medida = match.group(4)

        if qtd > 0:
            preco_por_unidade = price / qtd
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

    elif re.search(patterns['rolo_simples'], unit_str_lower):
        match = re.search(patterns['rolo_simples'], unit_str_lower)
        rolos = float(match.group(1))
        if rolos > 0:
            return {'preco_por_rolo': price / rolos, 'unidade': f"{int(rolos) if rolos.is_integer() else rolos} rolos"}

    elif re.search(patterns['metro'], unit_str_lower):
        match = re.search(patterns['metro'], unit_str_lower)
        metros = float(match.group(1))
        if metros > 0:
            return {'preco_por_metro': price / metros, 'unidade': f"{int(metros) if metros.is_integer() else metros}m"}

    elif re.search(patterns['folhas'], unit_str_lower):
        match = re.search(patterns['folhas'], unit_str_lower)
        folhas = float(match.group(1))
        if folhas > 0:
            return {'preco_por_folha': price / folhas, 'unidade': f"{int(folhas) if folhas.is_integer() else folhas} folhas"}

    elif re.search(patterns['embalagem_simples'], unit_str_lower):
        match = re.search(patterns['embalagem_simples'], unit_str_lower)
        qtd = float(match.group(1))
        tipo_embalagem = match.group(2)
        if qtd > 0:
            return {'preco_por_unidade': price / qtd, 'unidade': f"{int(qtd) if qtd.is_integer() else qtd} {tipo_embalagem}"}

    return {'preco_unitario': price, 'unidade': unit_str}

# === FUNÇÕES DE PESQUISA ===
async def show_search_results_direct(update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
    """Mostra os resultados da pesquisa direta de produto."""
    user_id = update.effective_user.id
    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)
        
        sheet = get_produtos_sheet(client)
        rows = sheet.get_all_values()[1:]
        matching_rows = [row for row in rows if row[COL_USER_GRUPO_ID] == grupo_id and row[COL_PRODUTO].lower().startswith(search_term.lower())]
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha para pesquisa direta: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    if not matching_rows:
        await update.message.reply_text(
            f"📭 Produto '*{search_term}*' não encontrado na sua lista.\n\n"
            "Você pode adicioná-lo usando o botão *➕ Adicionar Produto*.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    message = f"🔍 Resultados para '*{search_term}*':\n\n"
    produtos_agrupados = {}
    for row in matching_rows:
         nome = row[COL_PRODUTO]
         if nome not in produtos_agrupados:
             produtos_agrupados[nome] = []
         produtos_agrupados[nome].append(row)

    for nome, registros in produtos_agrupados.items():
        message += f"🏷️ *{nome}*\n"
        for registro in registros:
            tipo = registro[COL_TIPO] if len(registro) > COL_TIPO else "N/A"
            marca = registro[COL_MARCA] if len(registro) > COL_MARCA else "N/A"
            unidade = registro[COL_UNIDADE] if len(registro) > COL_UNIDADE else "N/A"
            preco = registro[COL_PRECO] if len(registro) > COL_PRECO else "N/A"
            obs = registro[COL_OBS] if len(registro) > COL_OBS else ""
            preco_por_unidade = registro[COL_PRECO_UNID] if len(registro) > COL_PRECO_UNID else "N/A"
            timestamp = registro[COL_TIMESTAMP] if len(registro) > COL_TIMESTAMP else "N/A"

            message += f"  📦 {tipo} | 🏭 {marca}\n"
            message += f"  📏 {unidade} | 💵 R$ {preco}\n"
            if preco_por_unidade and preco_por_unidade != "N/A":
                message += f"  📊 {preco_por_unidade}\n"
            if obs:
                message += f"  📝 {obs}\n"
            message += f"  🕒 {timestamp}\n---\n"

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
    # Verifica se foi chamado com um código de convite
    if context.args and len(context.args) > 0:
        codigo_convite = context.args[0]
        if codigo_convite.startswith("convite_"):
            context.user_data['invite_code_to_accept'] = codigo_convite
            await update.message.reply_text(
                f"Você foi convidado para um grupo!\n"
                f"Código do convite: `{codigo_convite}`\n"
                f"Digite /aceitar para confirmar ou /cancelar.",
                parse_mode="Markdown"
            )
            return AWAIT_INVITE_CODE # Novo estado para aceitar convite
    
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
    # Mensagem de ajuda atualizada com botão de compartilhamento
    help_text = (
        "🛒 Como adicionar um produto corretamente:\n"
        "Use o seguinte formato (uma linha por produto):\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n\n"
        "*Exemplos:*\n"
        "• Arroz, Branco, Camil, 5 kg, 25.99\n"
        "• Leite, Integral, Italac, 1 L, 4.49\n"
        "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90  ← Sem vírgula entre rolos e metros\n"
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
        "- Use o botão 👪 Compartilhar Lista para convidar outras pessoas."
    )
    # Criar um teclado inline com o botão de compartilhar
    keyboard = [[InlineKeyboardButton("👪 Compartilhar Lista", callback_data="compartilhar_lista")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        help_text,
        reply_markup=reply_markup, # Usar o teclado inline aqui
        parse_mode="Markdown"
    )
    # Manter o teclado principal também
    await update.message.reply_text("...", reply_markup=main_menu_keyboard())
    return MAIN_MENU

# === NOVA FUNÇÃO: COMPARTILHAR LISTA ===
async def compartilhar_lista_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'Compartilhar Lista'."""
    query = update.callback_query
    await query.answer() # Responde ao clique do botão
    
    user_id = query.from_user.id
    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)
        
        # Gera um código de convite baseado no grupo_id existente
        # Para simplificar, vamos usar o próprio grupo_id como código
        codigo_convite = grupo_id
        link_convite = f"https://t.me/{(await context.bot.get_me()).username}?start={codigo_convite}"
        
        # Primeira mensagem: link e texto
        await query.edit_message_text(
            f"🔗 Link de convite: {link_convite}\n\n"
            f"Caso prefira, compartilhe o código abaixo:"
        )
        
        # Segunda mensagem: código
        # Como não podemos editar para enviar outra mensagem, enviamos uma nova
        await query.message.reply_text(
            f"🔐 Código do grupo: `{codigo_convite}`",
            parse_mode="Markdown"
        )
        
        # Reenvia o menu principal
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())
        
    except Exception as e:
        logging.error(f"Erro ao gerar convite para user_id {user_id}: {e}")
        await query.edit_message_text("❌ Erro ao gerar convite. Tente novamente mais tarde.")
        await query.message.reply_text("...", reply_markup=main_menu_keyboard())

async def aceitar_convite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /aceitar para aceitar um convite recebido via link ou código."""
    user_id = update.effective_user.id
    codigo_convite = context.user_data.get('invite_code_to_accept')
    
    if not codigo_convite:
        await update.message.reply_text(
            "ℹ️ Nenhum convite pendente. Use um link de convite ou digite `/aceitar <codigo>`.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    try:
        client = get_sheet()
        sucesso, mensagem = await adicionar_usuario_ao_grupo(client, user_id, codigo_convite)
        
        if sucesso:
            await update.message.reply_text(mensagem, reply_markup=main_menu_keyboard())
            # Limpa o código pendente
            context.user_data.pop('invite_code_to_accept', None)
        else:
            await update.message.reply_text(f"❌ {mensagem}", reply_markup=main_menu_keyboard())
            
    except Exception as e:
        logging.error(f"Erro ao aceitar convite para user_id {user_id}: {e}")
        await update.message.reply_text(
            "❌ Erro ao processar o convite. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

async def ask_product_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Digite os dados do produto no formato:\n"
        "*Produto, Tipo, Marca, Unidade, Preço, Observações*\n\n"
        "*Exemplos:*\n"
        "• Arroz, Branco, Camil, 5 kg, 25.99\n"
        "• Leite, Integral, Italac, 1 L, 4.49\n"
        "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90  ← Sem vírgula entre rolos e metros\n"
        "• Creme Dental, Sensitive, Colgate, 180g, 27.75, 3 tubos de 60g\n"
        "• Ovo, Branco, Grande, 30 und, 16.90\n\n"
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
            "• Papel Higiênico, Compacto, Max, 12 rolos 30M, 14.90  ← Sem vírgula entre rolos e metros\n"
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
        'observacoes': data[5].strip() if len(data) > 5 else ""
    }

    unit_info = calculate_unit_price(product['unidade'], price)

    message = (
        f"🔍 Confirme os dados do produto:\n"
        f"🏷️ *Produto*: {product['nome']}\n"
        f"📦 *Tipo*: {product['tipo']}\n"
        f"🏭 *Marca*: {product['marca']}\n"
        f"📏 *Unidade*: {product['unidade']}\n"
        f"💵 *Preço*: R$ {product['preco']}\n"
    )
    if product['observacoes']:
        message += f"📝 *Observações*: {product['observacoes']}\n"

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

    user_id = update.effective_user.id
    product = context.user_data.get('current_product')
    unit_info = context.user_data.get('unit_info')

    if not product or not unit_info:
         await update.message.reply_text(
            "❌ Erro ao salvar produto. Dados não encontrados.",
            reply_markup=main_menu_keyboard()
        )
         return MAIN_MENU

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
        unit_price_str = f"R$ {format_price(parse_price(product['preco']))}/unidade"

    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id) # Obtém o grupo_id do usuário
        
        sheet = get_produtos_sheet(client)
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
    context.user_data.clear()
    return MAIN_MENU

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)
        
        sheet = get_produtos_sheet(client)
        rows = sheet.get_all_values()[1:]
        user_rows = [row for row in rows if row[COL_USER_GRUPO_ID] == grupo_id]
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    if not user_rows:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado na sua lista.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    message = "📋 *Lista de Produtos*\n"
    for row in user_rows:
        nome = row[COL_PRODUTO] if len(row) > COL_PRODUTO else "N/A"
        tipo = row[COL_TIPO] if len(row) > COL_TIPO else "N/A"
        marca = row[COL_MARCA] if len(row) > COL_MARCA else "N/A"
        unidade = row[COL_UNIDADE] if len(row) > COL_UNIDADE else "N/A"
        preco = row[COL_PRECO] if len(row) > COL_PRECO else "N/A"
        preco_por_unidade = row[COL_PRECO_UNID] if len(row) > COL_PRECO_UNID else "N/A"

        message += (
            f"🏷️ *{nome}* ({tipo})\n"
            f"🏭 {marca} | 📏 {unidade} | 💵 R$ {preco}\n"
            f"📊 {preco_por_unidade}\n\n"
        )

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

async def edit_or_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permite selecionar um produto para editar ou excluir."""
    user_id = update.effective_user.id
    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)
        
        sheet = get_produtos_sheet(client)
        rows = sheet.get_all_values()[1:]
        user_rows = [row for row in rows if row[COL_USER_GRUPO_ID] == grupo_id]
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha para editar/excluir: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    produtos = list(set([row[COL_PRODUTO] for row in user_rows]))
    if not produtos:
        await update.message.reply_text(
            "📭 Nenhum produto cadastrado para editar ou excluir na sua lista.",
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

    user_id = update.effective_user.id
    produto_nome = update.message.text.strip()
    try:
        client = get_sheet()
        grupo_id = await get_grupo_id(client, user_id)
        
        sheet = get_produtos_sheet(client)
        all_rows = sheet.get_all_values()
        rows = all_rows[1:]
        user_rows = [row for row in rows if row[COL_USER_GRUPO_ID] == grupo_id]
    except Exception as e:
        logging.error(f"Erro ao acessar a planilha para confirmar editar/excluir: {e}")
        await update.message.reply_text(
            "❌ Erro ao acessar os produtos. Tente novamente mais tarde.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    matching_entries = []
    for i, row in enumerate(rows):
        if row[COL_USER_GRUPO_ID] == grupo_id and row[COL_PRODUTO] == produto_nome:
            matching_entries.append({
                'sheet_index': i + 2,
                'data': row
            })

    if not matching_entries:
        await update.message.reply_text(
            f"ℹ️ Produto '{produto_nome}' não encontrado na sua lista.",
            reply_markup=main_menu_keyboard()
        )
        return MAIN_MENU

    context.user_data['selected_product_name'] = produto_nome
    context.user_data['matching_entries'] = matching_entries

    if len(matching_entries) == 1:
        entry = matching_entries[0]
        row_data = entry['data']
        message = f"✏️ Produto selecionado:\n"
        message += f"🏷️ *{row_data[COL_PRODUTO]}* ({row_data[COL_TIPO]})\n"
        message += f"🏭 {row_data[COL_MARCA]}\n"
        message += f"📏 {row_data[COL_UNIDADE]}\n"
        message += f"💵 *Preço*: R$ {row_data[COL_PRECO]}\n"
        if len(row_data) > COL_OBS and row_data[COL_OBS]:
            message += f"📝 {row_data[COL_OBS]}\n"
        if len(row_data) > COL_PRECO_UNID and row_data[COL_PRECO_UNID]:
            message += f"📊 {row_data[COL_PRECO_UNID]}\n"
        if len(row_data) > COL_TIMESTAMP and row_data[COL_TIMESTAMP]:
            message += f"🕒 {row_data[COL_TIMESTAMP]}\n"

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

    else:
        message = f"🔍 Foram encontradas múltiplas entradas para *{produto_nome}*:\n\n"
        buttons = []
        for i, entry in enumerate(matching_entries):
            row_data = entry['data']
            tipo = row_data[COL_TIPO] if len(row_data) > COL_TIPO else "N/A"
            marca = row_data[COL_MARCA] if len(row_data) > COL_MARCA else "N/A"
            unidade = row_data[COL_UNIDADE] if len(row_data) > COL_UNIDADE else "N/A"
            preco = row_data[COL_PRECO] if len(row_data) > COL_PRECO else "N/A"
            
            message += f"{i+1}. {tipo} | {marca} | {unidade} | R$ {preco}\n"
            buttons.append([KeyboardButton(str(i+1))])
        
        message += "\nSelecione o número da entrada que deseja editar/excluir:"
        buttons.append([KeyboardButton("❌ Cancelar")])
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
        return AWAIT_ENTRY_CHOICE

async def handle_multiple_entry_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lida com a escolha do usuário quando há múltiplas entradas, via botão ou texto."""
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)
    
    try:
        choice_text = update.message.text.strip()
        choice = int(choice_text)
        matching_entries = context.user_data.get('matching_entries', [])
        if 1 <= choice <= len(matching_entries):
            selected_entry = matching_entries[choice - 1]
            row_data = selected_entry['data']
            
            message = f"✏️ Entrada selecionada:\n"
            message += f"🏷️ *{row_data[COL_PRODUTO]}* ({row_data[COL_TIPO]})\n"
            message += f"🏭 {row_data[COL_MARCA]}\n"
            message += f"📏 {row_data[COL_UNIDADE]}\n"
            message += f"💵 *Preço*: R$ {row_data[COL_PRECO]}\n"
            if len(row_data) > COL_OBS and row_data[COL_OBS]:
                message += f"📝 {row_data[COL_OBS]}\n"
            if len(row_data) > COL_PRECO_UNID and row_data[COL_PRECO_UNID]:
                message += f"📊 {row_data[COL_PRECO_UNID]}\n"
            if len(row_data) > COL_TIMESTAMP and row_data[COL_TIMESTAMP]:
                message += f"🕒 {row_data[COL_TIMESTAMP]}\n"
                
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
        produto_nome = context.user_data.get('selected_product_name', 'desconhecido')
        matching_entries = context.user_data.get('matching_entries', [])
        if not matching_entries:
            await update.message.reply_text(
                "❌ Erro ao processar a escolha. Por favor, tente novamente.",
                reply_markup=main_menu_keyboard()
            )
            return MAIN_MENU
            
        message = f"⚠️ Escolha inválida. Selecione uma das opções para *{produto_nome}*:\n\n"
        buttons = []
        for i, entry in enumerate(matching_entries):
            row_data = entry['data']
            tipo = row_data[COL_TIPO] if len(row_data) > COL_TIPO else "N/A"
            marca = row_data[COL_MARCA] if len(row_data) > COL_MARCA else "N/A"
            unidade = row_data[COL_UNIDADE] if len(row_data) > COL_UNIDADE else "N/A"
            preco = row_data[COL_PRECO] if len(row_data) > COL_PRECO else "N/A"
            
            message += f"{i+1}. {tipo} | {marca} | {unidade} | R$ {preco}\n"
            buttons.append([KeyboardButton(str(i+1))])
        
        message += "\nSelecione o número da entrada que deseja editar/excluir:"
        buttons.append([KeyboardButton("❌ Cancelar")])
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
        )
        return AWAIT_ENTRY_CHOICE

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
        client = get_sheet()
        sheet = get_produtos_sheet(client)
        
        all_rows = sheet.get_all_values()
        if sheet_index > len(all_rows):
             raise IndexError("Linha não encontrada")
             
        row_data = all_rows[sheet_index - 1]
        
        sheet.update_cell(sheet_index, COL_PRECO + 1, new_price_str) # +1 por causa do 1-based do gspread
        
        unidade_str = row_data[COL_UNIDADE] if len(row_data) > COL_UNIDADE else ""
        unit_info = calculate_unit_price(unidade_str, new_price)
        
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
            
        sheet.update_cell(sheet_index, COL_PRECO_UNID + 1, unit_price_str)
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        sheet.update_cell(sheet_index, COL_TIMESTAMP + 1, timestamp)
        
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
            client = get_sheet()
            sheet = get_produtos_sheet(client)
            sheet.delete_rows(sheet_index)
            await update.message.reply_text(
                f"🗑️ Produto foi excluído permanentemente da sua lista.",
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
    context.user_data.clear()
    return MAIN_MENU

async def search_product_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 Digite o *nome* (ou parte inicial do nome) do produto para pesquisar:",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown"
    )
    return SEARCH_PRODUCT_INPUT

async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Cancelar":
        return await cancel(update, context)

    search_term = update.message.text.strip().title()
    return await show_search_results_direct(update, context, search_term)

async def handle_direct_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_term = update.message.text.strip()
    
    menu_buttons = ["➕ Adicionar Produto", "✏️ Editar ou Excluir", "📋 Listar Produtos", "🔍 Pesquisar Produto", "ℹ️ Ajuda", "❌ Cancelar"]
    if search_term in menu_buttons:
        return MAIN_MENU
    
    return await show_search_results_direct(update, context, search_term)

# === CONVERSATION HANDLER ===
def build_conv_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("aceitar", aceitar_convite), # Novo comando
            MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_data),
            MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
            MessageHandler(filters.Regex("^✏️ Editar ou Excluir$"), edit_or_delete_product),
            MessageHandler(filters.Regex("^🔍 Pesquisar Produto$"), search_product_history),
            MessageHandler(filters.Regex("^ℹ️ Ajuda$"), show_help),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct_search)
        ],
        states={
            MAIN_MENU: [
                MessageHandler(filters.Regex("^➕ Adicionar Produto$"), ask_product_data),
                MessageHandler(filters.Regex("^✏️ Editar ou Excluir$"), edit_or_delete_product),
                MessageHandler(filters.Regex("^📋 Listar Produtos$"), list_products),
                MessageHandler(filters.Regex("^🔍 Pesquisar Produto$"), search_product_history),
                MessageHandler(filters.Regex("^ℹ️ Ajuda$"), show_help),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_direct_search)
            ],
            AWAIT_PRODUCT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_data)],
            CONFIRM_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_product)],
            AWAIT_DELETION_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_edit_delete),
            ],
            AWAIT_ENTRY_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_multiple_entry_choice)
            ],
            AWAIT_EDIT_DELETE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_delete_choice)],
            AWAIT_EDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_price)],
            CONFIRM_DELETION: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_deletion)],
            SEARCH_PRODUCT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, show_search_results)],
            AWAIT_INVITE_CODE: [ # Novo estado
                CommandHandler("aceitar", aceitar_convite),
                CommandHandler("cancelar", cancel), # Ou MessageHandler para /cancelar
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: MAIN_MENU) # Ignora outros textos
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancelar$"), cancel)],
        # Adicionar handler para callbacks (botões inline)
        per_message=False # Permite que callbacks sejam processados
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
    conv_handler = build_conv_handler()
    application.add_handler(conv_handler)
    
    # Adicionar handler específico para callbacks (botões inline)
    application.add_handler(CallbackQueryHandler(compartilhar_lista_callback, pattern="^compartilhar_lista$"))
    
    await application.initialize()
    webhook_url = f"{os.environ['RENDER_EXTERNAL_URL']}/webhook"
    await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    logging.info(f"Webhook configurado: {webhook_url}")
    await application.start()
    while True:
        await asyncio.sleep(3600)

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
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
