import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from fpdf import FPDF
import io
import qrcode
import tempfile
from PIL import Image
import urllib.parse
import re
import hashlib
import secrets
import os
from pathlib import Path

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="CMMS MARX", layout="wide", page_icon="🏭")

# --- BANCO DE DADOS ---
def _resolve_db_path():
    """Usa DB existente na pasta do script; instalações novas vão para %LOCALAPPDATA% (evita corrupção no Drive)."""
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / 'manutencao_v21.db',
        Path.cwd() / 'manutencao_v21.db',
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    local_dir = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CMMS_MARX'
    local_dir.mkdir(parents=True, exist_ok=True)
    return str(local_dir / 'manutencao_v21.db')

DB_NAME = _resolve_db_path()

# --- SEGURANÇA: SENHAS ---
PWD_PREFIX = "pbkdf2_sha256$"
PWD_ITERATIONS = 260000

def hash_password(password):
    if not password:
        return ""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), PWD_ITERATIONS)
    return f"{PWD_PREFIX}{PWD_ITERATIONS}${salt}${dk.hex()}"

def is_password_hashed(stored):
    return bool(stored and str(stored).startswith(PWD_PREFIX))

def verify_password(password, stored):
    if not stored or password is None:
        return False
    stored = str(stored)
    if is_password_hashed(stored):
        try:
            _, iterations, salt_hex, expected_hex = stored.split('$', 3)
            salt = bytes.fromhex(salt_hex)
            dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(iterations))
            return secrets.compare_digest(dk.hex(), expected_hex)
        except (ValueError, TypeError):
            return False
    return secrets.compare_digest(password, stored)

def sql_like(term):
    if not term:
        return None
    escaped = str(term).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f"%{escaped}%"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS empresa (id INTEGER PRIMARY KEY, nome TEXT, endereco TEXT, telefone TEXT, email TEXT, logo BLOB)''')
    try: c.execute("ALTER TABLE empresa ADD COLUMN cnpj TEXT")
    except: pass
    try: c.execute("ALTER TABLE empresa ADD COLUMN cidade TEXT")
    except: pass
    try: c.execute("ALTER TABLE empresa ADD COLUMN estado TEXT")
    except: pass
    try: c.execute("ALTER TABLE empresa ADD COLUMN cep TEXT")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS fornecedores (id INTEGER PRIMARY KEY, nome TEXT, contato TEXT, telefone TEXT, email TEXT)''')
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN cnpj TEXT")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN endereco TEXT")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN cidade TEXT")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN estado TEXT")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN cep TEXT")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN ativo INTEGER DEFAULT 1")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN categoria TEXT DEFAULT ''")
    except: pass
    try: c.execute("ALTER TABLE fornecedores ADD COLUMN apresentacao TEXT DEFAULT ''")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS categorias_fornecedores (id INTEGER PRIMARY KEY, nome TEXT UNIQUE)''')

    c.execute("SELECT count(*) FROM categorias_fornecedores")
    if c.fetchone()[0] == 0:
        default_cats = [("Elétrica",), ("Mecânica",), ("Serviços",), ("EPIs",), ("Ferramentas",), ("Usinagem",), ("Geral",)]
        c.executemany("INSERT OR IGNORE INTO categorias_fornecedores (nome) VALUES (?)", default_cats)
        conn.commit()

    try:
        c.execute("INSERT OR IGNORE INTO categorias_fornecedores (nome) SELECT DISTINCT categoria FROM fornecedores WHERE categoria IS NOT NULL AND categoria != ''")
        conn.commit()
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS ativos (id INTEGER PRIMARY KEY, nome TEXT, setor TEXT, data_aquisicao TEXT, descricao TEXT, imagem BLOB)''')
    try: c.execute("ALTER TABLE ativos ADD COLUMN ativo INTEGER DEFAULT 1")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS ativos_pecas (id INTEGER PRIMARY KEY, ativo_id INTEGER, peca_id INTEGER)''')

    c.execute('''CREATE TABLE IF NOT EXISTS estoque (id INTEGER PRIMARY KEY, nome TEXT, qtd INTEGER, minimo INTEGER, preco REAL, imagem BLOB)''')
    try: c.execute("ALTER TABLE estoque ADD COLUMN unidade TEXT DEFAULT 'Un'")
    except: pass
    try: c.execute("ALTER TABLE estoque ADD COLUMN ativo INTEGER DEFAULT 1")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS estoque_fornecedores (id INTEGER PRIMARY KEY, peca_id INTEGER, fornecedor_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT, nome_completo TEXT, telefone TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS os (
                    id INTEGER PRIMARY KEY,
                    ativo_id INTEGER,
                    funcionario_id INTEGER,
                    descricao TEXT,
                    data_abertura TEXT,
                    data_inicio TEXT,
                    data_fechamento TEXT,
                    status TEXT,
                    criado_por TEXT,
                    editado_por TEXT
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS os_pecas (id INTEGER PRIMARY KEY, os_id INTEGER, peca_id INTEGER, qtd_usada INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS os_fotos (id INTEGER PRIMARY KEY, os_id INTEGER, imagem BLOB)''')

    c.execute('''CREATE TABLE IF NOT EXISTS compras (
                    id INTEGER PRIMARY KEY,
                    fornecedor_id INTEGER,
                    data_pedido TEXT,
                    status TEXT,
                    total_estimado REAL,
                    tipo TEXT,
                    criado_por TEXT,
                    editado_por TEXT
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS compras_itens (id INTEGER PRIMARY KEY, compra_id INTEGER, peca_id INTEGER, qtd_pedida INTEGER, preco_unitario REAL)''')

    c.execute('''CREATE TABLE IF NOT EXISTS preventiva (id INTEGER PRIMARY KEY, ativo_id INTEGER, tarefa TEXT, frequencia_dias INTEGER, ultima_execucao TEXT, proxima_execucao TEXT)''')
    try: c.execute("ALTER TABLE preventiva ADD COLUMN pop_id INTEGER DEFAULT 0")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS projetos (
                    id INTEGER PRIMARY KEY,
                    nome TEXT,
                    descricao TEXT,
                    status TEXT,
                    link_drive TEXT,
                    data_criacao TEXT,
                    criado_por TEXT
                )''')
    try: c.execute("ALTER TABLE projetos ADD COLUMN data_inicio TEXT")
    except: pass
    try: c.execute("ALTER TABLE projetos ADD COLUMN data_fim TEXT")
    except: pass
    try: c.execute("ALTER TABLE projetos ADD COLUMN orcamento REAL")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS projeto_usuarios (
                    id INTEGER PRIMARY KEY,
                    projeto_id INTEGER,
                    usuario_id INTEGER
                )''')

    c.execute('''CREATE TABLE IF NOT EXISTS projeto_tarefas (
                    id INTEGER PRIMARY KEY,
                    projeto_id INTEGER,
                    descricao TEXT,
                    responsavel_id INTEGER,
                    data_limite TEXT,
                    status TEXT
                )''')
    try: c.execute("ALTER TABLE projeto_tarefas ADD COLUMN observacoes TEXT")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS pop_modelos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, criado_por TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pop_passos (id INTEGER PRIMARY KEY, pop_id INTEGER, passo TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS os_checklist (id INTEGER PRIMARY KEY, os_id INTEGER, passo TEXT, concluido INTEGER DEFAULT 0)''')

    c.execute('''CREATE TABLE IF NOT EXISTS notas_fiscais (
                    id INTEGER PRIMARY KEY,
                    data_compra TEXT,
                    descricao TEXT,
                    quantidade REAL,
                    fornecedor TEXT,
                    valor_total REAL,
                    status_pagamento TEXT,
                    criado_por TEXT
                )''')

    # Tabela filha para guardar múltiplos itens por Nota
    c.execute('''CREATE TABLE IF NOT EXISTS notas_fiscais_itens (
                    id INTEGER PRIMARY KEY,
                    nota_id INTEGER,
                    descricao TEXT,
                    quantidade REAL,
                    valor_unitario REAL
                )''')

    c.execute("SELECT count(*) FROM notas_fiscais_itens")
    if c.fetchone()[0] == 0:
        c.execute("SELECT count(*) FROM notas_fiscais")
        if c.fetchone()[0] > 0:
            try:
                c.execute("INSERT INTO notas_fiscais_itens (nota_id, descricao, quantidade, valor_unitario) SELECT id, descricao, quantidade, (valor_total / CASE WHEN quantidade > 0 THEN quantidade ELSE 1 END) FROM notas_fiscais WHERE descricao IS NOT NULL AND descricao != ''")
                conn.commit()
            except: pass

    # ADMIN PADRÃO (senha inicial: 1234 — altere em Meu Perfil após o primeiro acesso)
    try:
        c.execute("INSERT INTO usuarios (username, password, role, nome_completo, telefone) VALUES (?, ?, ?, ?, ?)",
                  ("admin", hash_password("1234"), "admin", "Administrador Principal", ""))
        conn.commit()
    except sqlite3.IntegrityError:
        pass

    # Migra senhas antigas em texto puro para hash
    c.execute("SELECT id, password FROM usuarios WHERE password IS NOT NULL AND password != ''")
    for uid, pwd in c.fetchall():
        if pwd and not is_password_hashed(pwd):
            c.execute("UPDATE usuarios SET password=? WHERE id=?", (hash_password(pwd), uid))
    conn.commit()

    # EMPRESA PADRÃO
    c.execute("SELECT count(*) FROM empresa")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO empresa (nome, endereco, telefone, email) VALUES (?, ?, ?, ?)",
                  ("Minha Indústria", "Endereço da Fábrica", "(00) 0000-0000", "email@empresa.com"))
        conn.commit()

    # ATIVO "GERAL" AUTOMÁTICO
    c.execute("SELECT count(*) FROM ativos WHERE nome='GERAL'")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO ativos (nome, setor, data_aquisicao, descricao) VALUES (?, ?, ?, ?)",
                  ("GERAL", "Administrativo", datetime.now().strftime("%Y-%m-%d"), "Ativo genérico usado para manutenções prediais ou serviços avulsos (Não entra nas estatísticas)."))
        conn.commit()

    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_os_status ON os(status)",
        "CREATE INDEX IF NOT EXISTS idx_os_funcionario ON os(funcionario_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_ativo ON os(ativo_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_fotos_os ON os_fotos(os_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_pecas_os ON os_pecas(os_id)",
        "CREATE INDEX IF NOT EXISTS idx_compras_itens_compra ON compras_itens(compra_id)",
        "CREATE INDEX IF NOT EXISTS idx_os_checklist_os ON os_checklist(os_id)",
    ):
        try:
            c.execute(idx_sql)
        except sqlite3.Error:
            pass

    conn.commit()
    conn.close()

if 'db_initialized' not in st.session_state:
    init_db()
    st.session_state.db_initialized = True

# --- SISTEMA DE LOGIN ---
def check_login(username, password):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id, username, role, nome_completo, password FROM usuarios WHERE username=?", (username,))
        row = c.fetchone()
        if row and verify_password(password, row[4]):
            if not is_password_hashed(row[4]):
                c.execute("UPDATE usuarios SET password=? WHERE id=?", (hash_password(password), row[0]))
                conn.commit()
            conn.close()
            return row[:4]
        conn.close()
        return None
    except Exception:
        return None

def login_page():
    st.markdown("<br><br><h1 style='text-align: center;'>🔒 Acesso CMMS MARX</h1>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        with st.form("login_form", clear_on_submit=True):
            user = st.text_input("Utilizador")
            password = st.text_input("Palavra-passe", type="password")
            submit = st.form_submit_button("Entrar")
            if submit:
                user_data = check_login(user, password)
                if user_data:
                    st.session_state['logged_in'] = True
                    st.session_state['user_id'] = user_data[0]
                    st.session_state['username'] = user_data[1]
                    st.session_state['role'] = user_data[2]
                    st.session_state['name'] = user_data[3]
                    st.success("Login efetuado!")
                    st.rerun()
                else:
                    st.error("Dados incorretos.")

def logout():
    st.session_state['logged_in'] = False
    st.rerun()

if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if not st.session_state['logged_in']:
    login_page()
    st.stop()

# ==============================================================================
# FUNÇÕES DO SISTEMA E INTEGRAÇÕES
# ==============================================================================

def format_date_br(date_str):
    if not date_str: return ""
    try:
        if '-' in str(date_str) and len(str(date_str)) == 10:
            return datetime.strptime(str(date_str), "%Y-%m-%d").strftime("%d/%m/%Y")
        return str(date_str)
    except:
        return str(date_str)

@st.cache_resource
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    return conn

def run_query(query, params=(), return_data=False):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(query, params)
        if return_data:
            return c.fetchall()
        conn.commit()
        return c.lastrowid
    except Exception as e:
        st.error(f"Erro BD: {e}")
        return [] if return_data else False

# --- FUNÇÃO PARA CALCULAR KPI ENGENHARIA (MTBF / MTTR) ---
@st.cache_data(ttl=120)
def calcular_kpis_engenharia(ativo_id, dias_retroativos=90):
    query = """
        SELECT data_inicio, data_fechamento
        FROM os
        WHERE ativo_id = ? AND status = 'Concluída'
        AND data_inicio IS NOT NULL AND data_fechamento IS NOT NULL
        AND data_inicio != '' AND data_fechamento != ''
        AND (descricao NOT LIKE '%[PREVENTIVA]%' OR descricao IS NULL)
    """
    dados = run_query(query, (ativo_id,), return_data=True)

    limite_dt = datetime.now() - timedelta(days=dias_retroativos)

    tempos_reparo = []
    intervalos_falha = []
    ultima_data_fechamento = None

    parsed_dados = []
    for d_ini_str, d_fim_str in dados:
        try:
            d_ini = datetime.strptime(d_ini_str, "%d/%m/%Y %H:%M")
            d_fim = datetime.strptime(d_fim_str, "%d/%m/%Y %H:%M")
            parsed_dados.append((d_ini, d_fim))
        except:
            pass

    parsed_dados.sort(key=lambda x: x[0])

    for d_ini, d_fim in parsed_dados:
        if d_fim < limite_dt:
            ultima_data_fechamento = d_fim
            continue

        duracao = (d_fim - d_ini).total_seconds() / 3600.0
        if duracao >= 0:
            tempos_reparo.append(duracao)

        if ultima_data_fechamento:
            intervalo = (d_ini - ultima_data_fechamento).total_seconds() / 3600.0
            if intervalo >= 0:
                intervalos_falha.append(intervalo)

        ultima_data_fechamento = d_fim

    if not tempos_reparo or not intervalos_falha:
        return None

    mttr = sum(tempos_reparo) / len(tempos_reparo)
    mtbf = sum(intervalos_falha) / len(intervalos_falha)
    disponibilidade = (mtbf / (mtbf + mttr)) * 100 if (mtbf + mttr) > 0 else 0

    return {
        "mttr": mttr,
        "mtbf": mtbf,
        "disponibilidade": disponibilidade,
        "total_falhas": len(tempos_reparo)
    }

def _classificar_disponibilidade(pct):
    if pct >= 95:
        return "Excelente", "🟢"
    if pct >= 85:
        return "Bom", "🟡"
    if pct >= 70:
        return "Atenção", "🟠"
    return "Crítico", "🔴"

def _classificar_mttr(horas):
    if horas < 2:
        return "Rápido", "🟢"
    if horas < 8:
        return "Aceitável", "🟡"
    if horas < 24:
        return "Lento", "🟠"
    return "Muito lento", "🔴"

def _classificar_mtbf(horas):
    if horas >= 500:
        return "Alta confiabilidade", "🟢"
    if horas >= 168:
        return "Regular", "🟡"
    if horas >= 48:
        return "Baixa", "🟠"
    return "Muito baixa", "🔴"

def _montar_df_kpis_engenharia(ativos_lista, dias):
    linhas = []
    for ativo_id, ativo_nome in ativos_lista:
        stats = calcular_kpis_engenharia(ativo_id, dias)
        if stats:
            disp = round(stats["disponibilidade"], 2)
            mtbf = round(stats["mtbf"], 2)
            mttr = round(stats["mttr"], 2)
            disp_lbl, disp_ico = _classificar_disponibilidade(disp)
            mtbf_lbl, mtbf_ico = _classificar_mtbf(mtbf)
            mttr_lbl, mttr_ico = _classificar_mttr(mttr)
            linhas.append({
                "Máquina": ativo_nome,
                "MTBF (h)": mtbf,
                "MTTR (h)": mttr,
                "Disponibilidade (%)": disp,
                "Falhas no período": stats["total_falhas"],
                "Nível disponibilidade": f"{disp_ico} {disp_lbl}",
                "Nível MTBF": f"{mtbf_ico} {mtbf_lbl}",
                "Nível MTTR": f"{mttr_ico} {mttr_lbl}",
                "_tem_dados": True,
            })
        else:
            linhas.append({
                "Máquina": ativo_nome,
                "MTBF (h)": None,
                "MTTR (h)": None,
                "Disponibilidade (%)": None,
                "Falhas no período": 0,
                "Nível disponibilidade": "⚪ Sem dados",
                "Nível MTBF": "⚪ Sem dados",
                "Nível MTTR": "⚪ Sem dados",
                "_tem_dados": False,
            })
    return pd.DataFrame(linhas)

@st.cache_data(ttl=300)
def get_empresa():
    data = run_query("SELECT nome, endereco, telefone, email, logo, cnpj, cidade, estado, cep FROM empresa WHERE id=1", return_data=True)
    return data[0] if data else ("Empresa", "", "", "", None, "", "", "", "")

@st.cache_data(ttl=120)
def listar_tecnicos_os():
    return run_query(
        "SELECT id, nome_completo FROM usuarios WHERE role IN ('operador', 'supervisor', 'admin')",
        return_data=True,
    )

@st.cache_data(ttl=120)
def listar_fornecedores_ativos():
    return run_query("SELECT id, nome FROM fornecedores WHERE ativo=1", return_data=True)

def get_ativo_imagem(ativo_id):
    if not ativo_id:
        return None
    row = run_query("SELECT imagem FROM ativos WHERE id=?", (ativo_id,), return_data=True)
    return row[0][0] if row else None

def get_estoque_imagem(peca_id):
    if not peca_id:
        return None
    row = run_query("SELECT imagem FROM estoque WHERE id=?", (peca_id,), return_data=True)
    return row[0][0] if row else None

def invalidate_read_caches():
    get_empresa.clear()
    listar_tecnicos_os.clear()
    listar_fornecedores_ativos.clear()
    calcular_kpis_engenharia.clear()

def converter_df_para_excel(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Dados')
    return output.getvalue()

def criar_imagem_qr(dados):
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(dados)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    img.save(temp_file.name)
    return temp_file.name

def otimizar_imagem(upload_file, max_size=(800, 800), quality=80):
    if not upload_file: return None
    try:
        img = Image.open(upload_file)
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality)
        return output.getvalue()
    except Exception as e:
        st.error(f"Aviso: Não foi possível comprimir a imagem ({e}).")
        upload_file.seek(0)
        return upload_file.read()

def salvar_imagem_temp(blob_imagem, resize=True):
    try:
        image = Image.open(io.BytesIO(blob_imagem))
        if image.mode in ("RGBA", "P"): image = image.convert("RGB")
        if resize: image.thumbnail((800, 800))
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        image.save(temp_file.name, format="JPEG", quality=95)
        return temp_file.name
    except: return None

def gerar_link_whatsapp(telefone_bruto, mensagem):
    if not telefone_bruto: return None
    num = re.sub(r'\D', '', str(telefone_bruto))
    if not num: return None
    if len(num) <= 11: num = "55" + num
    msg_encoded = urllib.parse.quote(mensagem)
    return f"https://wa.me/{num}?text={msg_encoded}"

def ui_botao_pdf(col, cache_key, gerar_fn, nome_arquivo, btn_key, dl_key):
    """Um único widget por coluna (gerar OU baixar) — evita erro removeChild do Streamlit/React."""
    with col:
        if st.session_state.get(cache_key):
            st.download_button(
                label="⬇️ Baixar PDF",
                data=st.session_state[cache_key],
                file_name=nome_arquivo,
                mime="application/pdf",
                key=dl_key,
            )
        elif st.button("📄 Gerar PDF", key=btn_key):
            st.session_state[cache_key] = gerar_fn()
            st.rerun()

# --- GERADOR DE PDF ---
class PDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def cabecalho_pdf(pdf, empresa_dados):
    def txt(t): return str(t).encode('latin-1', 'replace').decode('latin-1')
    offset_x = 10
    if empresa_dados[4]:
        logo_path = salvar_imagem_temp(empresa_dados[4], resize=False)
        if logo_path:
            try:
                pdf.image(logo_path, x=10, y=8, h=25)
                offset_x = 50
            except: pass

    pdf.set_xy(offset_x, 10)
    pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 8, txt(empresa_dados[0]), ln=True)

    pdf.set_x(offset_x)
    pdf.set_font('Arial', '', 7)
    cnpj_str = f"CNPJ: {empresa_dados[5]}" if empresa_dados[5] else ""
    pdf.cell(0, 5, txt(f"{cnpj_str} | Tel: {empresa_dados[2]} | Email: {empresa_dados[3]}"), ln=True)

    pdf.set_x(offset_x)
    end_str = f"{empresa_dados[1]}"
    if empresa_dados[6] or empresa_dados[7]:
        end_str += f" - {empresa_dados[6]}/{empresa_dados[7]}"
    if empresa_dados[8]:
        end_str += f" - CEP: {empresa_dados[8]}"
    pdf.cell(0, 5, txt(end_str), ln=True)

    pdf.line(10, 35, 200, 35)
    pdf.set_y(40)

def gerar_pdf_oc(oc_id):
    empresa = get_empresa()
    oc_data_list = run_query(f"""
        SELECT c.id, f.nome, f.contato, f.telefone, f.email, c.data_pedido, c.total_estimado, c.tipo, c.criado_por, c.editado_por, f.cnpj, f.endereco, f.cidade, f.estado
        FROM compras c LEFT JOIN fornecedores f ON c.fornecedor_id = f.id WHERE c.id = {oc_id}
    """, return_data=True)
    if not oc_data_list: return None
    oc_data = oc_data_list[0]

    tipo_doc = str(oc_data[7]) if oc_data[7] else "Padrão"
    mostrar_preco = tipo_doc in ["Padrão", "Autorização de Serviço", "Reparo"]

    itens = run_query(f"""
        SELECT e.nome, i.qtd_pedida, i.preco_unitario, e.unidade
        FROM compras_itens i LEFT JOIN estoque e ON i.peca_id = e.id WHERE i.compra_id = {oc_id}
    """, return_data=True)

    pdf = PDF()
    pdf.add_page()
    def txt(t): return str(t).encode('latin-1', 'replace').decode('latin-1')

    cabecalho_pdf(pdf, empresa)

    f_nome = oc_data[1] if oc_data[1] else "A Definir / Não informado"
    qr_texto = f"DOC: {tipo_doc}\nID: {oc_data[0]}\nForn: {f_nome}\nData: {oc_data[5]}\nTotal: {oc_data[6]}"
    qr_path = criar_imagem_qr(qr_texto)
    pdf.image(qr_path, x=170, y=8, w=25)

    titulo = "ORDEM DE COMPRA"
    if "Cotação" in tipo_doc: titulo = "SOLICITAÇÃO DE COTAÇÃO"
    elif "Emergencial" in tipo_doc: titulo = "PEDIDO EMERGENCIAL"
    elif "Autorização" in tipo_doc: titulo = "AUTORIZAÇÃO DE SERVIÇO"
    elif "Reparo" in tipo_doc: titulo = "ORDEM DE REPARO"

    pdf.set_font('Arial', 'B', 14)
    pdf.cell(130, 10, txt(titulo), 0, 0)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(60, 10, txt(f"Nº {oc_data[0]}"), 0, 1, 'R')
    pdf.set_font('Arial', '', 10)
    pdf.cell(190, 6, txt(f"Emissão: {oc_data[5]}"), 0, 1, 'R')
    pdf.ln(5)
    pdf.set_fill_color(240,240,240)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 8, txt(" DADOS DO FORNECEDOR / PRESTADOR"), 1, 1, 'L', fill=True)
    pdf.set_font('Arial', '', 9)
    pdf.cell(120, 6, txt(f"Nome: {f_nome}"), 0, 0)
    pdf.cell(70, 6, txt(f"CNPJ: {oc_data[10] or '-'}"), 0, 1)

    end_forn_str = f"Endereço: {oc_data[11] or '-'}"
    if oc_data[12]: end_forn_str += f", {oc_data[12]}"
    if oc_data[13]: end_forn_str += f"-{oc_data[13]}"
    pdf.cell(0, 6, txt(end_forn_str), 0, 1)

    contato_str = f"Contato: {oc_data[2] or '-'} | Tel: {oc_data[3] or '-'} | Email: {oc_data[4] or '-'}"
    pdf.cell(0, 6, txt(contato_str), 0, 1)
    pdf.ln(5)

    pdf.set_font('Arial', 'B', 10)

    if mostrar_preco:
        pdf.cell(90, 8, txt("Item / Serviço"), 1, 0, 'L', fill=True)
        pdf.cell(30, 8, txt("Qtd/Unid"), 1, 0, 'C', fill=True)
        pdf.cell(35, 8, txt("Unit. (R$)"), 1, 0, 'R', fill=True)
        pdf.cell(35, 8, txt("Subtotal (R$)"), 1, 1, 'R', fill=True)
    else:
        pdf.cell(160, 8, txt("Item / Serviço"), 1, 0, 'L', fill=True)
        pdf.cell(30, 8, txt("Qtd/Unid"), 1, 1, 'C', fill=True)

    pdf.set_font('Arial', '', 10)
    total_calc = 0
    for i in itens:
        nm = i[0] if i[0] else "Item Excluído"
        unid = i[3] if i[3] else "Un"
        qtd_txt = f"{i[1]} {unid}"

        if mostrar_preco:
            preco_unit = i[2] if i[2] is not None else 0.0
            tot = i[1] * preco_unit
            total_calc += tot
            pdf.cell(90, 7, txt(nm), 1)
            pdf.cell(30, 7, txt(qtd_txt), 1, 0, 'C')
            pdf.cell(35, 7, f"{preco_unit:.2f}", 1, 0, 'R')
            pdf.cell(35, 7, f"{tot:.2f}", 1, 1, 'R')
        else:
            pdf.cell(160, 7, txt(nm), 1)
            pdf.cell(30, 7, txt(qtd_txt), 1, 1, 'C')

    if mostrar_preco:
        pdf.ln(2)
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(155, 10, txt("TOTAL DA ORDEM:"), 0, 0, 'R')
        pdf.cell(35, 10, f"R$ {total_calc:.2f}", 0, 1, 'R')

    pdf.ln(10)
    pdf.set_font('Arial', 'I', 8)
    rastreio_txt = f"Emitido por: {oc_data[8] if oc_data[8] else 'Sistema'}"
    if oc_data[9]: rastreio_txt += f" | {oc_data[9]}"
    pdf.cell(0, 5, txt(rastreio_txt), 0, 1, 'L')

    pdf.ln(15)
    y = pdf.get_y()
    pdf.line(20, y, 90, y)
    pdf.line(120, y, 190, y)
    pdf.set_font('Arial', '', 9)
    pdf.cell(95, 5, txt("Assinatura do Solicitante"), 0, 0, 'C')
    pdf.cell(95, 5, txt("Aprovação Diretoria"), 0, 1, 'C')

    saida_pdf = pdf.output(dest='S')
    if isinstance(saida_pdf, str):
        return saida_pdf.encode('latin-1')
    return bytes(saida_pdf)

def gerar_pdf_os(os_id):
    empresa = get_empresa()
    os_data_list = run_query(f"""
        SELECT os.id, a.nome, u.nome_completo, os.descricao, os.data_abertura, os.data_fechamento, os.status, a.descricao, os.criado_por, os.editado_por, os.data_inicio
        FROM os
        LEFT JOIN ativos a ON os.ativo_id = a.id
        LEFT JOIN usuarios u ON os.funcionario_id = u.id
        WHERE os.id = {os_id}
    """, return_data=True)
    if not os_data_list: return None
    os_data = os_data_list[0]

    pecas = run_query(f"SELECT e.nome, op.qtd_usada, e.unidade FROM os_pecas op JOIN estoque e ON op.peca_id=e.id WHERE op.os_id={os_id}", return_data=True)
    fotos = run_query(f"SELECT imagem FROM os_fotos WHERE os_id={os_id}", return_data=True)
    checklists = run_query(f"SELECT passo, concluido FROM os_checklist WHERE os_id={os_id}", return_data=True)

    pdf = PDF()
    pdf.add_page()
    def txt(t): return str(t).encode('latin-1', 'replace').decode('latin-1')

    cabecalho_pdf(pdf, empresa)

    equip_nome = os_data[1] or "Ativo Não Encontrado"
    tec_nome = os_data[2] or "Técnico Não Atribuído"

    qr_texto = f"OS: {os_data[0]}\nMaq: {equip_nome}\nTec: {tec_nome}\nStat: {os_data[6]}"
    qr_path = criar_imagem_qr(qr_texto)
    pdf.image(qr_path, x=170, y=8, w=28)
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(130, 10, txt("ORDEM DE SERVIÇO / AGENDAMENTO"), 0, 0)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(60, 10, txt(f"Nº {os_data[0]}"), 0, 1, 'R')
    pdf.ln(5)
    pdf.set_fill_color(240,240,240)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 8, txt(" DETALHES"), 1, 1, 'L', fill=True)
    pdf.set_font('Arial', '', 10)
    pdf.ln(2)
    pdf.cell(100, 6, txt(f"Equipamento: {equip_nome}"), 0, 0)
    pdf.cell(90, 6, txt(f"Técnico: {tec_nome}"), 0, 1)
    if os_data[7]:
        pdf.set_font('Arial', 'I', 8)
        pdf.cell(0, 4, txt(f"({os_data[7]})"), 0, 1)
        pdf.set_font('Arial', '', 10)

    pdf.cell(60, 6, txt(f"Data Programada: {os_data[4]}"), 0, 0)
    pdf.cell(70, 6, txt(f"Início Real: {os_data[10] or 'Pendente'}"), 0, 0)
    pdf.cell(60, 6, txt(f"Status: {os_data[6]}"), 0, 1)
    pdf.ln(5)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 8, txt(" DESCRIÇÃO DO SERVIÇO / HISTÓRICO"), 1, 1, 'L', fill=True)
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 6, txt(os_data[3] or ""), border=1)
    pdf.ln(5)

    if checklists:
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 8, txt(" CHECKLIST / PROCEDIMENTO PADRÃO (POP)"), 1, 1, 'L', fill=True)
        pdf.set_font('Arial', '', 10)
        for chk in checklists:
            box = "[ X ]" if chk[1] == 1 else "[   ]"
            pdf.cell(0, 6, txt(f"{box} {chk[0]}"), 0, 1, 'L')
        pdf.ln(5)

    if pecas:
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 8, txt(" PEÇAS UTILIZADAS"), 1, 1, 'L', fill=True)
        pdf.set_font('Arial', '', 10)
        pdf.cell(150, 7, txt("Item"), 1, 0, 'L')
        pdf.cell(40, 7, txt("Qtd"), 1, 1, 'C')
        for p in pecas:
            unid_str = p[2] if p[2] else "Un"
            pdf.cell(150, 7, txt(p[0]), 1, 0, 'L')
            pdf.cell(40, 7, txt(f"{p[1]} {unid_str}"), 1, 1, 'C')

    if fotos:
        pdf.ln(5)
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 8, txt(" REGISTRO FOTOGRÁFICO"), 1, 1, 'L', fill=True)
        pdf.ln(5)
        for f in fotos:
            img_path = salvar_imagem_temp(f[0])
            if img_path:
                if pdf.get_y() > 250: pdf.add_page()
                try:
                    pdf.image(img_path, w=80)
                    pdf.ln(5)
                except: pass

    pdf.ln(10)
    pdf.set_font('Arial', 'I', 8)
    rastreio_txt = f"Emitido por: {os_data[8] if os_data[8] else 'Sistema'}"
    if os_data[9]: rastreio_txt += f" | {os_data[9]}"
    pdf.cell(0, 5, txt(rastreio_txt), 0, 1, 'L')

    pdf.ln(15)
    y = pdf.get_y()
    pdf.line(20, y, 90, y)
    pdf.line(120, y, 190, y)
    pdf.set_font('Arial', '', 9)
    pdf.cell(95, 5, txt("Técnico Executante"), 0, 0, 'C')
    pdf.cell(95, 5, txt("Gestor/Supervisor"), 0, 1, 'C')

    saida_pdf = pdf.output(dest='S')
    if isinstance(saida_pdf, str):
        return saida_pdf.encode('latin-1')
    return bytes(saida_pdf)

# ==============================================================================
# MENU LATERAL COM CONTROLE DE ACESSO AVANÇADO
# ==============================================================================
user_role = st.session_state['role']

empresa_info = get_empresa()
if empresa_info[4]:
    try:
        logo_img = Image.open(io.BytesIO(empresa_info[4]))
        st.sidebar.image(logo_img, use_container_width=True)
    except: pass

st.sidebar.write(f"👤 **{st.session_state['name']}** ({user_role.upper()})")

if user_role in ['admin', 'supervisor']:
    alertas_os = run_query("SELECT count(*) FROM os WHERE status = 'Aberta'", return_data=True)[0][0]
else:
    alertas_os = run_query("SELECT count(*) FROM os WHERE status = 'Aberta' AND funcionario_id = ?", (st.session_state['user_id'],), return_data=True)[0][0]

if alertas_os > 0:
    st.sidebar.error(f"🚨 **Você tem {alertas_os} O.S. aguardando ação!**")
    if st.session_state.get('last_alertas_os') != alertas_os:
        st.toast(f"🚨 {alertas_os} O.S. Aberta(s) pendentes!", icon="⚠️")
    st.session_state['last_alertas_os'] = alertas_os
else:
    st.session_state['last_alertas_os'] = 0

if st.sidebar.button("Sair (Logout)"): logout()
st.sidebar.markdown("---")

menus_comuns = ["Painel Geral", "Agenda", "Projetos", "Meu Perfil"]

if user_role == 'admin':
    opcoes = menus_comuns + ["Estatísticas de Engenharia", "Manutenção Preventiva", "Ordens de Serviço (OS)", "Ordens de Compra (OC)", "Lançamento de Notas", "Estoque", "Ativos", "Fornecedores", "Checklists (POPs)", "Gestão Usuários", "Config. Empresa"]
elif user_role == 'supervisor':
    opcoes = menus_comuns + ["Estatísticas de Engenharia", "Manutenção Preventiva", "Ordens de Serviço (OS)", "Ordens de Compra (OC)", "Lançamento de Notas", "Estoque", "Ativos", "Fornecedores", "Checklists (POPs)"]
else:
    opcoes = menus_comuns + ["Ordens de Serviço (OS)"]

menu = st.sidebar.radio("Navegação", opcoes)

st.sidebar.markdown("---")
st.sidebar.markdown("###### Idealizado por **Felício Marques**")

# ==============================================================================
# LÓGICA DAS PÁGINAS
# ==============================================================================

if menu == "Meu Perfil":
    st.title("👤 Meu Perfil")
    st.write(f"**Nome:** {st.session_state['name']}")
    st.write(f"**Nível de Acesso:** {st.session_state['role'].upper()}")
    st.divider()

    st.subheader("🔑 Alterar Palavra-passe")
    with st.form("change_pwd", clear_on_submit=True):
        pwd_atual = st.text_input("Palavra-passe Atual", type="password")
        pwd_nova = st.text_input("Nova Palavra-passe", type="password")
        pwd_conf = st.text_input("Confirmar Nova Palavra-passe", type="password")

        if st.form_submit_button("Atualizar Palavra-passe"):
            user_valid = check_login(st.session_state['username'], pwd_atual)
            if user_valid:
                if pwd_nova == pwd_conf and len(pwd_nova) > 0:
                    run_query("UPDATE usuarios SET password=? WHERE username=?", (hash_password(pwd_nova), st.session_state['username']))
                    st.success("Palavra-passe alterada com sucesso! Use a nova senha no próximo login.")
                else:
                    st.error("Erro: A nova palavra-passe está vazia ou não coincide com a confirmação.")
            else:
                st.error("A palavra-passe atual está incorreta.")

elif menu == "Painel Geral":
    st.title("📊 Painel de Controle")
    try:
        tot_os = run_query("SELECT count(*) FROM os LEFT JOIN ativos a ON os.ativo_id = a.id WHERE os.status IN ('Aberta', 'Iniciada') AND IFNULL(a.nome, '') != 'GERAL'", return_data=True)[0][0]
        tot_crit = run_query("SELECT count(*) FROM estoque WHERE qtd < minimo", return_data=True)[0][0]
        hj = datetime.now().date()
        prevs = run_query("SELECT proxima_execucao FROM preventiva", return_data=True)
        tot_prev_atrasada = 0
        for p in prevs:
            try:
                if datetime.strptime(p[0], "%Y-%m-%d").date() < hj: tot_prev_atrasada += 1
            except: pass
    except: tot_os, tot_crit, tot_prev_atrasada = 0, 0, 0

    c1, c2, c3 = st.columns(3)
    c1.metric("OS Ativas (Produção)", tot_os)
    c2.metric("Prev. Atrasadas", tot_prev_atrasada, delta="Crítico" if tot_prev_atrasada > 0 else "OK", delta_color="inverse")
    c3.metric("Stock Crítico", tot_crit, delta="- Urgente" if tot_crit > 0 else "OK", delta_color="inverse")

    st.divider()

    col_kpi1, col_kpi2 = st.columns(2)

    with col_kpi1:
        st.subheader("⚠️ Top: Máquinas com Mais O.S.")
        q_top_ativos = """
            SELECT IFNULL(a.nome, 'Ativo Excluído') as Maquina, COUNT(os.id) as total_os
            FROM os
            LEFT JOIN ativos a ON os.ativo_id = a.id
            WHERE os.status != 'Cancelada' AND IFNULL(a.nome, '') != 'GERAL'
            GROUP BY os.ativo_id
            ORDER BY total_os DESC
        """
        top_ativos = run_query(q_top_ativos, return_data=True)

        if top_ativos:
            df_top_ativos = pd.DataFrame(top_ativos, columns=["Máquina", "Nº de O.S."])
            st.dataframe(df_top_ativos.head(5), use_container_width=True)
            st.download_button("📥 Baixar Relatório Completo (Máquinas)", converter_df_para_excel(df_top_ativos), "relatorio_maquinas_os.xlsx", key="btn_maquinas")
        else:
            st.info("Nenhuma O.S. válida registrada ainda (O Ativo 'GERAL' não entra aqui).")

    with col_kpi2:
        st.subheader("⚙️ Top: Peças Mais Utilizadas")
        q_top_pecas = """
            SELECT IFNULL(e.nome, 'Peça Excluída') as Peca, SUM(op.qtd_usada) as total_usado
            FROM os_pecas op
            LEFT JOIN estoque e ON op.peca_id = e.id
            JOIN os ON op.os_id = os.id
            WHERE os.status != 'Cancelada'
            GROUP BY op.peca_id
            ORDER BY total_usado DESC
        """
        top_pecas = run_query(q_top_pecas, return_data=True)

        if top_pecas:
            df_top_pecas = pd.DataFrame(top_pecas, columns=["Peça", "Quantidade Utilizada"])
            st.dataframe(df_top_pecas.head(5), use_container_width=True)
            st.download_button("📥 Baixar Relatório Completo (Peças)", converter_df_para_excel(df_top_pecas), "relatorio_consumo_pecas.xlsx", key="btn_pecas")
        else:
            st.info("Nenhuma peça utilizada em O.S. válida ainda.")

    st.divider()

    if tot_crit > 0:
        st.error(f"⚠️ Existem {tot_crit} itens abaixo do estoque mínimo!")
        crit_data = run_query("SELECT id, nome, qtd, minimo, (minimo - qtd) as falta FROM estoque WHERE qtd < minimo ORDER BY falta DESC", return_data=True)
        if crit_data:
            df_crit = pd.DataFrame(crit_data, columns=["ID", "Peça", "Qtd Atual", "Qtd Mínima", "Defasagem"])
            st.dataframe(df_crit.style.map(lambda x: 'background-color: #ffcccc', subset=['Defasagem']), use_container_width=True)

            if user_role in ['admin', 'supervisor']:
                if st.button("🚀 Gerar Cotação Automática no Sistema"):
                    dt_hoje = datetime.now().strftime("%d/%m/%Y")
                    cid = run_query("INSERT INTO compras (fornecedor_id, data_pedido, status, total_estimado, tipo, criado_por) VALUES (?,?,?,?,?,?)",
                                    (None, dt_hoje, 'Pendente', 0.0, 'Cotação', st.session_state['name']))
                    for item in crit_data:
                        run_query("INSERT INTO compras_itens (compra_id, peca_id, qtd_pedida, preco_unitario) VALUES (?,?,?,?)",
                                  (cid, item[0], item[4], 0.0))

                    st.success(f"Cotação Automática Nº {cid} gerada! Acesse a aba 'Ordens de Compra' para definir o Fornecedor e enviar.")
                    st.rerun()

    if user_role in ['admin', 'supervisor']:
        st.subheader("📋 Documentos Pendentes")
        ocs_pendentes = run_query("""
            SELECT c.id, IFNULL(f.nome, 'A Definir'), c.data_pedido, c.tipo, c.total_estimado
            FROM compras c LEFT JOIN fornecedores f ON c.fornecedor_id = f.id
            WHERE c.status = 'Pendente' ORDER BY c.id DESC
        """, return_data=True)
        if ocs_pendentes:
            df_ocs = pd.DataFrame(ocs_pendentes, columns=["ID", "Fornecedor", "Data", "Tipo", "Valor Est."])
            st.dataframe(df_ocs, use_container_width=True)
        else: st.info("Nada pendente.")

elif menu == "Estatísticas de Engenharia":
    st.title("📈 Estatísticas de Engenharia")
    st.caption("Indicadores de confiabilidade (MTBF), reparo (MTTR) e disponibilidade operacional das máquinas.")

    ativos_lista = run_query("SELECT id, nome FROM ativos WHERE ativo=1 AND nome != 'GERAL'", return_data=True)

    if not ativos_lista:
        st.info("Nenhum ativo disponível para análise.")
    else:
        with st.expander("📖 O que significam os indicadores?", expanded=False):
            st.markdown("""
**MTBF** (Mean Time Between Failures) — *Tempo médio entre falhas*  
Tempo médio em **horas** entre o fim de uma O.S. corretiva e o início da próxima no mesmo ativo.  
→ **Quanto maior, melhor** (a máquina demora mais a voltar a falhar).

**MTTR** (Mean Time To Repair) — *Tempo médio de reparo*  
Tempo médio em **horas** para concluir uma O.S. corretiva (do início ao fechamento).  
→ **Quanto menor, melhor** (reparos mais rápidos).

**Disponibilidade (%)** — *Percentagem de tempo útil*  
Estimada por: **MTBF ÷ (MTBF + MTTR) × 100**  
→ **Quanto maior, melhor** (menos paragens por falha).

**Base do cálculo:** apenas O.S. **concluídas**, com data de início e fechamento, nos últimos *N* dias.  
O.S. marcadas como preventiva (`[PREVENTIVA]` na descrição) **não entram** nestes KPIs.
            """)

        dias = st.slider("Período de análise (dias)", 30, 365, 90, help="Janela retroativa a partir de hoje.")

        df_kpi = _montar_df_kpis_engenharia(ativos_lista, dias)
        df_com_dados = df_kpi[df_kpi["_tem_dados"]].copy()
        n_com_dados = len(df_com_dados)
        n_sem_dados = len(df_kpi) - n_com_dados

        if n_com_dados == 0:
            st.warning(
                f"Nenhuma máquina tem O.S. corretivas concluídas (com início e fim) nos últimos **{dias}** dias. "
                "Conclua ordens de serviço corretivas para gerar MTBF, MTTR e disponibilidade."
            )
            st.dataframe(
                df_kpi.drop(columns=["_tem_dados"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            media_disp = df_com_dados["Disponibilidade (%)"].mean()
            media_mtbf = df_com_dados["MTBF (h)"].mean()
            media_mttr = df_com_dados["MTTR (h)"].mean()
            total_falhas = int(df_com_dados["Falhas no período"].sum())
            pior = df_com_dados.loc[df_com_dados["Disponibilidade (%)"].idxmin()]

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Máquinas com KPI", f"{n_com_dados} / {len(df_kpi)}")
            m2.metric("Disponibilidade média", f"{media_disp:.1f}%")
            m3.metric("MTBF médio (geral)", f"{media_mtbf:.1f} h")
            m4.metric("MTTR médio (geral)", f"{media_mttr:.1f} h")

            if n_sem_dados > 0:
                st.caption(f"⚪ {n_sem_dados} máquina(s) sem dados no período (precisam de ≥2 O.S. corretivas concluídas para intervalo entre falhas).")

            st.info(
                f"**Atenção no parque de máquinas:** menor disponibilidade em **{pior['Máquina']}** "
                f"({pior['Disponibilidade (%)']:.1f}% — {pior['Nível disponibilidade']}). "
                f"Total de **{total_falhas}** falhas corretivas registadas no período."
            )

            tab_graf, tab_tabela, tab_legenda = st.tabs(["📊 Gráficos", "📋 Tabela", "🎯 Referências"])

            with tab_graf:
                ordenar = st.radio(
                    "Ordenar gráficos por",
                    ["Disponibilidade (menor primeiro)", "MTBF (maior primeiro)", "MTTR (menor primeiro)", "Nome (A-Z)"],
                    horizontal=True,
                )
                df_chart = df_com_dados.sort_values("Máquina")
                if ordenar.startswith("Disponibilidade"):
                    df_chart = df_com_dados.sort_values("Disponibilidade (%)", ascending=True)
                elif ordenar.startswith("MTBF"):
                    df_chart = df_com_dados.sort_values("MTBF (h)", ascending=False)
                elif ordenar.startswith("MTTR"):
                    df_chart = df_com_dados.sort_values("MTTR (h)", ascending=True)

                g1, g2 = st.columns(2)
                with g1:
                    st.markdown("**Disponibilidade por máquina (%)**")
                    st.bar_chart(
                        df_chart.set_index("Máquina")[["Disponibilidade (%)"]],
                        height=280,
                        color="#2ecc71",
                    )
                    st.caption("Linha de referência mental: 85% = bom | 95% = excelente")
                with g2:
                    st.markdown("**MTTR — tempo médio de reparo (h)**")
                    st.bar_chart(
                        df_chart.set_index("Máquina")[["MTTR (h)"]],
                        height=280,
                        color="#e74c3c",
                    )
                    st.caption("Quanto mais baixa a barra, mais rápido o time resolve a falha.")

                st.markdown("**MTBF — tempo médio entre falhas (h)**")
                st.bar_chart(
                    df_chart.set_index("Máquina")[["MTBF (h)"]],
                    height=260,
                    color="#3498db",
                )
                st.caption("Quanto mais alta a barra, mais confiável é o ativo no período.")

            with tab_tabela:
                df_exibir = df_kpi.drop(columns=["_tem_dados"]).copy()
                df_export = df_exibir.copy()

                def _cor_disponibilidade(val):
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        return ""
                    try:
                        v = float(val)
                        if v >= 95:
                            return "background-color: #d4edda"
                        if v >= 85:
                            return "background-color: #fff3cd"
                        if v >= 70:
                            return "background-color: #ffe5cc"
                        return "background-color: #f8d7da"
                    except (TypeError, ValueError):
                        return ""

                styled = df_exibir.style.map(
                    _cor_disponibilidade,
                    subset=["Disponibilidade (%)"],
                )
                st.dataframe(styled, use_container_width=True, hide_index=True)

                st.download_button(
                    label="📥 Exportar para Excel",
                    data=converter_df_para_excel(df_export),
                    file_name=f"kpis_engenharia_{dias}_dias.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_kpi_eng",
                )

            with tab_legenda:
                st.subheader("Como ler os níveis automáticos")
                c_a, c_b, c_c = st.columns(3)
                with c_a:
                    st.markdown("""
**Disponibilidade**
| Nível | Faixa |
|-------|-------|
| 🟢 Excelente | ≥ 95% |
| 🟡 Bom | 85 – 94% |
| 🟠 Atenção | 70 – 84% |
| 🔴 Crítico | < 70% |
                    """)
                with c_b:
                    st.markdown("""
**MTBF** (entre falhas)
| Nível | Faixa |
|-------|-------|
| 🟢 Alta confiabilidade | ≥ 500 h |
| 🟡 Regular | 168 – 499 h |
| 🟠 Baixa | 48 – 167 h |
| 🔴 Muito baixa | < 48 h |
                    """)
                with c_c:
                    st.markdown("""
**MTTR** (reparo)
| Nível | Faixa |
|-------|-------|
| 🟢 Rápido | < 2 h |
| 🟡 Aceitável | 2 – 8 h |
| 🟠 Lento | 8 – 24 h |
| 🔴 Muito lento | > 24 h |
                    """)
                st.markdown("""
**Ações sugeridas**
- **Disponibilidade crítica:** priorizar preventiva, peças críticas (BOM) e análise de causa raiz.
- **MTTR alto:** treino da equipe, kit de ferramentas/peças na máquina, POP na O.S.
- **MTBF baixo:** investigar desgaste, operação, qualidade de insumos ou falha recorrente.
                """)

elif menu == "Agenda":
    st.title("📆 Agenda de Serviços (O.S.)")
    st.write("Acompanhe as manutenções agendadas e serviços em andamento na linha do tempo.")

    q_agenda = """
        SELECT os.id, a.nome, u.nome_completo, os.status, os.data_abertura, os.descricao
        FROM os
        LEFT JOIN ativos a ON os.ativo_id = a.id
        LEFT JOIN usuarios u ON os.funcionario_id = u.id
        WHERE os.status IN ('Aberta', 'Iniciada')
    """
    agenda_raw = run_query(q_agenda, return_data=True)

    if agenda_raw:
        parsed_agenda = []
        for item in agenda_raw:
            try:
                dt_obj = datetime.strptime(item[4], "%d/%m/%Y %H:%M")
            except:
                dt_obj = datetime.min
            parsed_agenda.append({'dt_obj': dt_obj, 'data': item})

        parsed_agenda.sort(key=lambda x: x['dt_obj'])

        for p in parsed_agenda:
            o = p['data']
            cor_bullet = "🔵" if o[3] == "Aberta" else "🟡"
            cor_borda = "#007bff" if o[3] == "Aberta" else "#ffc107"

            st.markdown(f"""
            <div style="padding:15px; border-radius:8px; background-color:#f8f9fa; border-left: 5px solid {cor_borda}; margin-bottom:10px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <h4 style="margin:0; padding:0; color:#333;">{cor_bullet} {o[4]} - {o[1] or 'Ativo Excluído'}</h4>
                <p style="margin:5px 0 0 0;"><strong>Técnico Responsável:</strong> {o[2] or 'Não Atribuído'} | <strong>Status:</strong> {o[3]}</p>
                <p style="margin:5px 0 0 0; color:#555;">{o[5][:150] + '...' if o[5] and len(o[5])>150 else (o[5] or 'Sem descrição do serviço.')}</p>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("🎉 Parabéns! Nenhuma ordem de serviço agendada ou pendente no momento.")

elif menu == "Lançamento de Notas":
    st.title("🧾 Notas Fiscais e Notinhas (Despesas Rápidas)")
    st.write("Lançou uma notinha com 5 itens? Adicione todos eles aqui! Depois, com um clique, você converte a loja num Fornecedor Oficial ou dá entrada de cada item individual no Estoque.")

    tab_lista, tab_nova = st.tabs(["Histórico de Notas Lançadas", "➕ Lançar Nova Nota"])

    with tab_lista:
        if 'page_notas' not in st.session_state: st.session_state.page_notas = 1

        col_search, col_status = st.columns([3, 1])
        termo = col_search.text_input("🔍 Buscar Nota", placeholder="Fornecedor...")
        filtro_status = col_status.selectbox("Status", ["Todas", "Pendente", "Pago"])

        q_count = "SELECT count(*) FROM notas_fiscais WHERE 1=1"
        params_count = []
        if termo:
            q_count += " AND fornecedor LIKE ? ESCAPE '\\'"
            params_count.append(sql_like(termo))
        if filtro_status != "Todas":
            q_count += " AND status_pagamento=?"
            params_count.append(filtro_status)

        total_items = run_query(q_count, tuple(params_count), return_data=True)[0][0]
        items_por_pagina = 10
        total_pages = max(1, (total_items + items_por_pagina - 1) // items_por_pagina)

        if st.session_state.page_notas > total_pages: st.session_state.page_notas = total_pages
        elif st.session_state.page_notas < 1: st.session_state.page_notas = 1

        offset = (st.session_state.page_notas - 1) * items_por_pagina

        q = "SELECT id, data_compra, fornecedor, valor_total, status_pagamento, criado_por FROM notas_fiscais WHERE 1=1"
        params_q = []
        if termo:
            q += " AND fornecedor LIKE ? ESCAPE '\\'"
            params_q.append(sql_like(termo))
        if filtro_status != "Todas":
            q += " AND status_pagamento=?"
            params_q.append(filtro_status)
        q += f" ORDER BY id DESC LIMIT {items_por_pagina} OFFSET {offset}"

        dados = run_query(q, tuple(params_q), return_data=True)

        if dados:
            st.caption(f"Mostrando notas da página {st.session_state.page_notas} de {total_pages} (Total: {total_items} registros).")
            st.divider()

            for row in dados:
                nid, ndata, nforn, nval, nstat, nuser = row
                cor_status = "🟢" if nstat == "Pago" else "🔴"

                # Puxa os itens daquela nota específica
                itens_nota = run_query(f"SELECT id, descricao, quantidade, valor_unitario FROM notas_fiscais_itens WHERE nota_id={nid}", return_data=True)
                qtd_total_itens = len(itens_nota) if itens_nota else 0

                with st.container(border=True):
                    col_info, col_val = st.columns([4, 1])

                    with col_info:
                        st.markdown(f"#### 🧾 Lançamento #{nid} - {nforn}")
                        st.write(f"**Data da Compra:** {format_date_br(ndata)} | **Itens na nota:** {qtd_total_itens}")
                        st.caption(f"Registado por: {nuser}")

                    with col_val:
                        st.markdown(f"<h3 style='color:#333;'>R$ {nval:.2f}</h3>", unsafe_allow_html=True)
                        st.write(f"{cor_status} {nstat}")

                    with st.expander("📦 Ver Itens Comprados / Opções e Integrações"):
                        st.write("### Itens desta Compra")
                        if itens_nota:
                            for itm in itens_nota:
                                # itm[0]=id, itm[1]=descricao, itm[2]=qtd, itm[3]=valor_unit
                                c1, c2, c3 = st.columns([3, 1, 2])
                                c1.write(f"🔹 **{itm[1]}** (Qtd: {itm[2]})")
                                c2.write(f"R$ {itm[3]:.2f} / un")

                                with c3.popover("⚙️ Cadastrar no Estoque"):
                                    st.write(f"Adicionar **{itm[1]}** ao controle de estoque.")
                                    with st.form(f"cad_est_{itm[0]}"):
                                        p_nome = st.text_input("Nome Oficial da Peça", value=itm[1])
                                        p_qtd = st.number_input("Estoque Atual", value=float(itm[2]), min_value=0.0)
                                        p_min = st.number_input("Estoque Mínimo", value=1)
                                        p_preco = st.number_input("Preço Unitário", value=float(itm[3]))
                                        p_unid = st.selectbox("Unidade", ["Un", "Kg", "Metros", "Litros", "Pares", "Caixa", "M²", "Serviço"])

                                        if st.form_submit_button("Salvar Item no Estoque"):
                                            run_query("INSERT INTO estoque (nome, qtd, minimo, preco, unidade, ativo) VALUES (?,?,?,?,?,?)", (p_nome, p_qtd, p_min, p_preco, p_unid, 1))
                                            st.success(f"{p_nome} cadastrado no Estoque!")
                                            st.rerun()
                        else:
                            st.warning("Nenhum item detalhado nesta nota.")

                        st.divider()
                        st.write("### Opções Gerais da Nota")
                        c_act1, c_act2, c_act3 = st.columns(3)

                        with c_act1.popover("💰 Alterar Status"):
                            with st.form(f"status_n_{nid}"):
                                novo_status = st.selectbox("Status do Pagamento", ["Pendente", "Pago"], index=1 if nstat=="Pago" else 0)
                                if st.form_submit_button("Salvar Status"):
                                    run_query("UPDATE notas_fiscais SET status_pagamento=? WHERE id=?", (novo_status, nid))
                                    st.rerun()

                            if st.button("🗑️ Excluir Nota Totalmente", key=f"del_n_{nid}"):
                                run_query(f"DELETE FROM notas_fiscais_itens WHERE nota_id={nid}")
                                run_query(f"DELETE FROM notas_fiscais WHERE id={nid}")
                                st.rerun()

                        with c_act2.popover("🚚 Salvar Fornecedor no Sistema"):
                            st.write("Adicione esta loja à sua base oficial de fornecedores.")
                            with st.form(f"cad_f_{nid}"):
                                f_nome = st.text_input("Razão Social / Nome Fantasia", value=nforn)
                                f_cnpj = st.text_input("CNPJ")

                                cat_opcoes_add = [c[1] for c in run_query("SELECT id, nome FROM categorias_fornecedores ORDER BY nome", return_data=True)]
                                if not cat_opcoes_add: cat_opcoes_add = ["Geral"]
                                f_categoria = st.selectbox("Categoria", cat_opcoes_add)

                                if st.form_submit_button("Cadastrar Fornecedor"):
                                    run_query("INSERT INTO fornecedores (nome, cnpj, categoria, ativo) VALUES (?,?,?,?)", (f_nome, f_cnpj, f_categoria, 1))
                                    st.success("Fornecedor cadastrado com sucesso! Acesse a aba Fornecedores para ver.")

            st.divider()
            c_prev, c_page, c_next = st.columns([1, 2, 1])
            if c_prev.button("⬅️ Página Anterior", key="prev_notas", disabled=(st.session_state.page_notas == 1)):
                st.session_state.page_notas -= 1
                st.rerun()
            with c_page:
                st.markdown(f"<div style='text-align: center;'><b>Página {st.session_state.page_notas} de {total_pages}</b></div>", unsafe_allow_html=True)
            if c_next.button("Próxima Página ➡️", key="next_notas", disabled=(st.session_state.page_notas == total_pages)):
                st.session_state.page_notas += 1
                st.rerun()

            st.divider()
            with st.expander("📥 Exportar Relatório de Despesas para Excel"):
                # Exportação simples do cabeçalho das notas
                df_export_q = "SELECT id, data_compra, fornecedor, valor_total, status_pagamento, criado_por FROM notas_fiscais"
                df_export_data = run_query(df_export_q, return_data=True)
                if df_export_data:
                    df = pd.DataFrame(df_export_data, columns=["ID", "Data da Compra", "Fornecedor / Loja", "Valor Total", "Status de Pagamento", "Registado Por"])
                    st.download_button("Baixar Planilha Excel", converter_df_para_excel(df), "relatorio_despesas_rapidas.xlsx")

        else:
            st.info("Nenhuma nota lançada ou encontrada para esta pesquisa.")

    with tab_nova:
        st.subheader("📝 Formulário de Lançamento (Notinha de Múltiplos Itens)")

        c_d, c_forn, c_stat = st.columns([1, 2, 1])
        n_data = c_d.date_input("Data da Compra", format="DD/MM/YYYY")
        n_forn = c_forn.text_input("Nome do Fornecedor / Loja *")
        n_stat = c_stat.selectbox("Status de Pagamento", ["Pendente", "Pago"])

        st.divider()
        if 'carrinho_notas' not in st.session_state: st.session_state.carrinho_notas = []

        with st.expander("➕ Adicionar Novo Item à Nota", expanded=True):
            c_desc, c_qtd, c_val, c_btn = st.columns([3, 1, 1, 1], vertical_alignment="bottom")
            i_desc = c_desc.text_input("Descrição do Item Comprado")
            i_qtd = c_qtd.number_input("Quantidade", min_value=0.01, value=1.0, key="i_qtd_nota")
            i_val = c_val.number_input("Preço Unitário (R$)", min_value=0.0, format="%.2f", key="i_val_nota")

            if c_btn.button("Adicionar Item"):
                if i_desc:
                    st.session_state.carrinho_notas.append({
                        "descricao": i_desc,
                        "quantidade": i_qtd,
                        "valor_unitario": i_val,
                        "total_item": i_qtd * i_val
                    })
                    st.rerun()
                else:
                    st.error("Preencha a descrição do item.")

        if st.session_state.carrinho_notas:
            st.write("### Carrinho da Nota")
            df_n = pd.DataFrame(st.session_state.carrinho_notas)

            df_show = df_n.copy()
            df_show.columns = ["Descrição do Item", "Quantidade", "Preço Unitário (R$)", "Total do Item (R$)"]
            st.dataframe(df_show, use_container_width=True)

            total_geral = df_n['total_item'].sum()
            st.markdown(f"### Valor Total da Nota: <span style='color:green;'>R$ {total_geral:.2f}</span>", unsafe_allow_html=True)

            c_limpar, c_salvar = st.columns([1, 4])
            if c_limpar.button("Limpar Carrinho"):
                st.session_state.carrinho_notas = []
                st.rerun()

            if c_salvar.button("✅ Confirmar Lançamento da Nota Completa", type="primary"):
                if n_forn:
                    nid = run_query("INSERT INTO notas_fiscais (data_compra, fornecedor, valor_total, status_pagamento, criado_por, descricao, quantidade) VALUES (?,?,?,?,?,?,?)",
                                    (str(n_data), n_forn, total_geral, n_stat, st.session_state['name'], "", 0))

                    for item in st.session_state.carrinho_notas:
                        run_query("INSERT INTO notas_fiscais_itens (nota_id, descricao, quantidade, valor_unitario) VALUES (?,?,?,?)",
                                  (nid, item["descricao"], item["quantidade"], item["valor_unitario"]))

                    st.session_state.carrinho_notas = []
                    st.success("Nota de múltiplos itens lançada com sucesso!")
                    st.rerun()
                else:
                    st.error("O Nome do Fornecedor / Loja é obrigatório!")

elif menu == "Checklists (POPs)":
    st.title("📋 POPs e Checklists Padrão")
    st.write("Crie procedimentos e listas de verificação obrigatórias para vincular às suas Ordens de Serviço.")

    tab_lista, tab_nova = st.tabs(["Gerenciar POPs", "Criar Novo POP"])

    with tab_lista:
        pops = run_query("SELECT id, nome, descricao, criado_por FROM pop_modelos", return_data=True)
        if pops:
            for p in pops:
                with st.expander(f"📖 {p[1]}"):
                    st.caption(f"Criado por: {p[3]}")
                    st.write(f"**Descrição:** {p[2]}")
                    st.divider()

                    st.write("##### 🛠️ Passos do Procedimento")
                    passos = run_query(f"SELECT id, passo FROM pop_passos WHERE pop_id={p[0]}", return_data=True)
                    if passos:
                        for passo in passos:
                            col_txt, col_del = st.columns([5, 1])
                            col_txt.write(f"☑️ {passo[1]}")
                            if col_del.button("Excluir", key=f"del_passo_{passo[0]}"):
                                run_query(f"DELETE FROM pop_passos WHERE id={passo[0]}")
                                st.rerun()
                    else:
                        st.info("Nenhum passo adicionado ainda.")

                    with st.form(f"add_passo_{p[0]}", clear_on_submit=True):
                        novo_passo = st.text_input("Adicionar novo passo ao Checklist:")
                        if st.form_submit_button("➕ Adicionar Passo"):
                            if novo_passo:
                                run_query("INSERT INTO pop_passos (pop_id, passo) VALUES (?,?)", (p[0], novo_passo))
                                st.rerun()

                    st.divider()
                    if st.button(f"🗑️ Excluir POP Inteiro", key=f"del_pop_{p[0]}"):
                        run_query(f"DELETE FROM pop_modelos WHERE id={p[0]}")
                        run_query(f"DELETE FROM pop_passos WHERE pop_id={p[0]}")
                        st.rerun()
        else:
            st.info("Nenhum POP cadastrado no sistema.")

    with tab_nova:
        with st.form("form_novo_pop", clear_on_submit=True):
            st.subheader("Criar novo Modelo de Procedimento")
            nome_pop = st.text_input("Nome do Procedimento (Ex: Revisão Mensal do Torno)")
            desc_pop = st.text_area("Descrição Breve / EPIs Necessários")

            if st.form_submit_button("Salvar Modelo Inicial"):
                if nome_pop:
                    run_query("INSERT INTO pop_modelos (nome, descricao, criado_por) VALUES (?,?,?)", (nome_pop, desc_pop, st.session_state['name']))
                    st.success("POP Criado! Vá na aba 'Gerenciar POPs' para adicionar as caixinhas de marcação (Passos).")
                else:
                    st.error("O nome do POP é obrigatório.")

elif menu == "Projetos":
    st.title("📂 Gestão de Projetos")

    if user_role in ['admin', 'supervisor']:
        tab_lista, tab_nova, tab_equipe = st.tabs(["Visão Geral e Tarefas", "Cadastrar Projeto", "Equipe do Projeto"])

        with tab_lista:
            projs = run_query("SELECT id, nome, descricao, status, link_drive, data_criacao, criado_por, data_inicio, data_fim, orcamento FROM projetos ORDER BY id DESC", return_data=True)
            if projs:
                for p in projs:
                    status_cor = "🟢" if p[3] == "Concluído" else "🟡" if p[3] == "Em Andamento" else "🔴" if p[3] == "Atrasado" else "⚪"

                    with st.expander(f"{status_cor} {p[1]} (Status: {p[3]})"):

                        total_tarefas = run_query(f"SELECT count(*) FROM projeto_tarefas WHERE projeto_id={p[0]}", return_data=True)[0][0]
                        concluidas = run_query(f"SELECT count(*) FROM projeto_tarefas WHERE projeto_id={p[0]} AND status='Concluída'", return_data=True)[0][0]
                        progresso = int((concluidas / total_tarefas) * 100) if total_tarefas > 0 else 0

                        st.progress(progresso, text=f"Progresso do Projeto: {progresso}% ({concluidas}/{total_tarefas} Tarefas Concluídas)")
                        st.divider()

                        c_info1, c_info2 = st.columns(2)
                        with c_info1:
                            st.write(f"**Objetivo:** {p[2]}")
                            st.caption(f"Criado por {p[6]} em {p[5]}")
                            if p[4]: st.link_button("🔗 Acessar Documentos (Drive)", p[4])
                        with c_info2:
                            dt_ini = format_date_br(p[7]) if p[7] else "Não definida"
                            dt_fim = format_date_br(p[8]) if p[8] else "Não definida"
                            orcamento = f"R$ {p[9]:.2f}" if p[9] else "Não definido"
                            st.write(f"**Início Previsto:** {dt_ini}")
                            st.write(f"**Fim Previsto:** {dt_fim}")
                            st.write(f"**Orçamento:** {orcamento}")

                        st.divider()

                        st.write("#### 📋 Cronograma de Tarefas")

                        tarefas = run_query(f"SELECT t.id, t.descricao, u.nome_completo, t.data_limite, t.status, t.responsavel_id, t.observacoes FROM projeto_tarefas t LEFT JOIN usuarios u ON t.responsavel_id = u.id WHERE t.projeto_id={p[0]} ORDER BY t.status DESC, t.data_limite ASC", return_data=True)

                        if tarefas:
                            for t in tarefas:
                                ct1, ct_edit, ct_btn = st.columns([6, 1, 1])
                                is_done = "✅" if t[4] == "Concluída" else "🔲"
                                resp = t[2] if t[2] else "Sem dono"
                                d_limite = format_date_br(t[3]) if t[3] else "S/ Data"

                                if t[4] == "Concluída":
                                    ct1.markdown(f"<span style='text-decoration: line-through; color: gray;'>{is_done} **{t[1]}** (Resp: {resp} | Prazo: {d_limite})</span>", unsafe_allow_html=True)
                                else:
                                    ct1.write(f"{is_done} **{t[1]}** (Resp: {resp} | Prazo: {d_limite})")

                                if t[6]:
                                    ct1.info(f"**Obs:** {t[6]}")

                                with ct_edit.popover("✏️"):
                                    with st.form(f"edit_tar_{t[0]}"):
                                        st.write("**Editar Tarefa**")
                                        n_desc_tar = st.text_input("Descrição", t[1])

                                        equipe_tar = run_query(f"SELECT u.id, u.nome_completo FROM projeto_usuarios pu JOIN usuarios u ON pu.usuario_id = u.id WHERE pu.projeto_id = {p[0]}", return_data=True)
                                        idx_resp = 0
                                        if equipe_tar and t[5]:
                                            for i, eq_u in enumerate(equipe_tar):
                                                if eq_u[0] == t[5]:
                                                    idx_resp = i
                                                    break
                                        n_resp_tar = st.selectbox("Responsável", equipe_tar, index=idx_resp, format_func=lambda x: x[1]) if equipe_tar else None

                                        try: d_lim_obj = datetime.strptime(t[3], "%Y-%m-%d").date()
                                        except: d_lim_obj = datetime.now().date()
                                        n_prazo_tar = st.date_input("Data Limite", d_lim_obj, format="DD/MM/YYYY")

                                        n_obs_tar = st.text_area("Observações / Progresso", t[6] or "")

                                        c_save, c_del = st.columns(2)
                                        if c_save.form_submit_button("💾 Salvar"):
                                            if n_resp_tar:
                                                run_query("UPDATE projeto_tarefas SET descricao=?, responsavel_id=?, data_limite=?, observacoes=? WHERE id=?", (n_desc_tar, n_resp_tar[0], str(n_prazo_tar), n_obs_tar, t[0]))
                                                st.rerun()
                                        if c_del.form_submit_button("🗑️ Excluir"):
                                            run_query(f"DELETE FROM projeto_tarefas WHERE id={t[0]}")
                                            st.rerun()

                                if t[4] != "Concluída" and ct_btn.button("Concluir", key=f"btn_t_done_{t[0]}"):
                                    run_query(f"UPDATE projeto_tarefas SET status='Concluída' WHERE id={t[0]}")
                                    st.rerun()
                                elif t[4] == "Concluída" and ct_btn.button("Reabrir", key=f"btn_t_re_{t[0]}"):
                                    run_query(f"UPDATE projeto_tarefas SET status='Pendente' WHERE id={t[0]}")
                                    st.rerun()
                        else:
                            st.info("Nenhuma tarefa cadastrada neste projeto.")

                        with st.popover("➕ Adicionar Nova Tarefa"):
                            equipe = run_query(f"SELECT u.id, u.nome_completo FROM projeto_usuarios pu JOIN usuarios u ON pu.usuario_id = u.id WHERE pu.projeto_id = {p[0]}", return_data=True)
                            if equipe:
                                with st.form(f"nova_tarefa_{p[0]}"):
                                    t_desc = st.text_input("Descrição da Tarefa *")
                                    t_resp = st.selectbox("Responsável", equipe, format_func=lambda x: x[1])
                                    t_prazo = st.date_input("Data Limite", format="DD/MM/YYYY")
                                    if st.form_submit_button("Criar Tarefa"):
                                        if t_desc:
                                            run_query("INSERT INTO projeto_tarefas (projeto_id, descricao, responsavel_id, data_limite, status) VALUES (?,?,?,?,?)",
                                                      (p[0], t_desc, t_resp[0], str(t_prazo), 'Pendente'))
                                            st.rerun()
                                        else:
                                            st.error("A descrição é obrigatória.")
                            else:
                                st.warning("Aloque pessoas na equipe do projeto antes de criar tarefas!")

                        st.divider()

                        with st.popover("⚙️ Configurações / Editar Projeto"):
                            with st.form(f"edit_proj_{p[0]}"):
                                n_nome = st.text_input("Nome", p[1])
                                n_desc = st.text_area("Descrição", p[2])

                                c_dt1, c_dt2 = st.columns(2)
                                try: d_i = datetime.strptime(p[7], "%Y-%m-%d").date()
                                except: d_i = datetime.now().date()
                                try: d_f = datetime.strptime(p[8], "%Y-%m-%d").date()
                                except: d_f = datetime.now().date()

                                n_ini = c_dt1.date_input("Data de Início", d_i, format="DD/MM/YYYY")
                                n_fim = c_dt2.date_input("Previsão de Término", d_f, format="DD/MM/YYYY")

                                n_orc = st.number_input("Orçamento Estimado (R$)", value=float(p[9] if p[9] else 0.0), min_value=0.0)

                                try: idx_status = ["Planejado", "Em Andamento", "Atrasado", "Concluído", "Pausado"].index(p[3])
                                except: idx_status = 0
                                n_status = st.selectbox("Status", ["Planejado", "Em Andamento", "Atrasado", "Concluído", "Pausado"], index=idx_status)
                                n_link = st.text_input("Link da Pasta (Google Drive)", p[4] or "")

                                if st.form_submit_button("Salvar Configurações"):
                                    run_query("UPDATE projetos SET nome=?, descricao=?, status=?, link_drive=?, data_inicio=?, data_fim=?, orcamento=? WHERE id=?",
                                              (n_nome, n_desc, n_status, n_link, str(n_ini), str(n_fim), n_orc, p[0]))
                                    st.success("Atualizado!")
                                    st.rerun()
            else:
                st.info("Nenhum projeto cadastrado no sistema.")

        with tab_nova:
            st.subheader("Cadastrar Novo Projeto de Engenharia/Melhoria")
            with st.form("novo_projeto", clear_on_submit=True):
                np_nome = st.text_input("Nome do Projeto *")
                np_desc = st.text_area("Descrição / Objetivo / Justificativa")

                c_d1, c_d2 = st.columns(2)
                np_ini = c_d1.date_input("Data de Início", format="DD/MM/YYYY")
                np_fim = c_d2.date_input("Data Final Prevista", format="DD/MM/YYYY")

                np_orc = st.number_input("Orçamento Estimado (R$)", min_value=0.0, value=0.0)

                np_status = st.selectbox("Status Inicial", ["Planejado", "Em Andamento"])
                np_link = st.text_input("Link para os Documentos (Google Drive, OneDrive, etc)")

                if st.form_submit_button("Criar Projeto"):
                    if np_nome:
                        dt_criacao = datetime.now().strftime("%d/%m/%Y")
                        run_query("INSERT INTO projetos (nome, descricao, status, link_drive, data_criacao, criado_por, data_inicio, data_fim, orcamento) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (np_nome, np_desc, np_status, np_link, dt_criacao, st.session_state['name'], str(np_ini), str(np_fim), np_orc))
                        st.success("Projeto criado! Vá na aba 'Alocar Equipe' para adicionar participantes e depois gere tarefas.")
                        st.rerun()
                    else:
                        st.error("O campo Nome do Projeto é obrigatório.")

        with tab_equipe:
            st.subheader("Gerenciar Participantes do Projeto")
            st.write("Operadores só conseguirão ver os projetos se forem adicionados aqui.")

            projs_db = run_query("SELECT id, nome FROM projetos", return_data=True)
            users_db = run_query("SELECT id, nome_completo, role FROM usuarios", return_data=True)

            if projs_db and users_db:
                c1, c2, c3 = st.columns([2,2,1])
                sel_p = c1.selectbox("Selecione o Projeto", projs_db, format_func=lambda x: x[1])
                sel_u = c2.selectbox("Selecione o Usuário", users_db, format_func=lambda x: f"{x[1]} ({x[2]})")

                c3.markdown("<br>", unsafe_allow_html=True)
                if c3.button("Alocar no Projeto", use_container_width=True):
                    check = run_query(f"SELECT count(*) FROM projeto_usuarios WHERE projeto_id={sel_p[0]} AND usuario_id={sel_u[0]}", return_data=True)[0][0]
                    if check == 0:
                        run_query("INSERT INTO projeto_usuarios (projeto_id, usuario_id) VALUES (?,?)", (sel_p[0], sel_u[0]))
                        st.success("Usuário adicionado ao projeto!")
                        st.rerun()
                    else:
                        st.warning("Este usuário já faz parte deste projeto.")

                st.divider()
                st.write(f"**Equipe atual do projeto selecionado acima:**")
                eq_list = run_query(f"SELECT pu.id, u.nome_completo, u.role FROM projeto_usuarios pu JOIN usuarios u ON pu.usuario_id=u.id WHERE pu.projeto_id={sel_p[0]}", return_data=True)

                if eq_list:
                    for eq in eq_list:
                        cx1, cx2 = st.columns([4,1])
                        cx1.write(f"- {eq[1]} ({eq[2]})")
                        if cx2.button("🗑️ Remover", key=f"rm_pu_{eq[0]}"):
                            run_query(f"DELETE FROM projeto_usuarios WHERE id={eq[0]}")
                            st.rerun()
                else:
                    st.info("Ninguém alocado ainda.")
            else:
                st.warning("É necessário ter projetos e usuários cadastrados para gerenciar equipes.")

    else:
        st.subheader("Meus Projetos & Minhas Tarefas")
        st.write("Abaixo estão os projetos onde você está alocado.")

        q_op = f"""
            SELECT p.id, p.nome, p.descricao, p.status, p.link_drive, p.data_criacao, p.data_inicio, p.data_fim
            FROM projetos p
            JOIN projeto_usuarios pu ON p.id = pu.projeto_id
            WHERE pu.usuario_id = {st.session_state['user_id']}
            ORDER BY p.id DESC
        """
        projetos_op = run_query(q_op, return_data=True)

        if projetos_op:
            for p in projetos_op:
                status_cor = "🟢" if p[3] == "Concluído" else "🟡" if p[3] == "Em Andamento" else "🔴" if p[3] == "Atrasado" else "⚪"
                with st.expander(f"{status_cor} {p[1]} (Status: {p[3]})"):

                    total_tarefas = run_query(f"SELECT count(*) FROM projeto_tarefas WHERE projeto_id={p[0]}", return_data=True)[0][0]
                    concluidas = run_query(f"SELECT count(*) FROM projeto_tarefas WHERE projeto_id={p[0]} AND status='Concluída'", return_data=True)[0][0]
                    progresso = int((concluidas / total_tarefas) * 100) if total_tarefas > 0 else 0

                    st.progress(progresso, text=f"Progresso Geral: {progresso}%")
                    st.divider()

                    st.write(f"**Descrição:** {p[2]}")
                    if p[4]:
                        st.link_button("🔗 Acessar Arquivos e Documentos (Drive)", p[4])

                    st.divider()
                    st.write("#### 📋 Minhas Tarefas neste Projeto")

                    tarefas = run_query(f"SELECT t.id, t.descricao, t.data_limite, t.status, t.observacoes FROM projeto_tarefas t WHERE t.projeto_id={p[0]} AND t.responsavel_id={st.session_state['user_id']} ORDER BY t.status DESC, t.data_limite ASC", return_data=True)

                    if tarefas:
                        for t in tarefas:
                            ct1, ct2 = st.columns([5,1])
                            is_done = "✅" if t[3] == "Concluída" else "🔲"
                            d_limite = format_date_br(t[2]) if t[2] else "S/ Data"

                            if t[3] == "Concluída":
                                ct1.markdown(f"<span style='text-decoration: line-through; color: gray;'>{is_done} **{t[1]}** (Prazo: {d_limite})</span>", unsafe_allow_html=True)
                            else:
                                ct1.write(f"{is_done} **{t[1]}** (Prazo: {d_limite})")

                            if t[4]:
                                ct1.info(f"**Obs:** {t[4]}")

                            with ct2.popover("📝 Ação"):
                                with st.form(f"op_tar_{t[0]}"):
                                    st.write("**Atualizar Tarefa**")
                                    op_obs = st.text_area("Anotações da Tarefa (Relato, pendências, etc.)", t[4] or "")
                                    op_status = st.selectbox("Status", ["Pendente", "Concluída"], index=1 if t[3] == "Concluída" else 0)

                                    if st.form_submit_button("Salvar"):
                                        run_query("UPDATE projeto_tarefas SET status=?, observacoes=? WHERE id=?", (op_status, op_obs, t[0]))
                                        st.rerun()
                    else:
                        st.info("Nenhuma tarefa atribuída a você neste projeto.")
        else:
            st.info("Você não foi adicionado a nenhum projeto no momento.")

elif menu == "Manutenção Preventiva":
    st.title("📅 Manutenção Preventiva")
    tab_lista, tab_nova = st.tabs(["Monitoramento & Gerar OS", "Gerenciar Planos (Editar/Excluir)"])

    with tab_lista:
        st.subheader("Cronograma de Manutenção")
        planos = run_query("""
            SELECT p.id, a.nome, p.tarefa, p.frequencia_dias, p.ultima_execucao, p.proxima_execucao, a.id, p.pop_id
            FROM preventiva p JOIN ativos a ON p.ativo_id = a.id
            ORDER BY p.proxima_execucao ASC
        """, return_data=True)

        if planos:
            hoje = datetime.now().date()
            for p in planos:
                try:
                    prox_data = datetime.strptime(p[5], "%Y-%m-%d").date()
                    dias_restantes = (prox_data - hoje).days
                except:
                    dias_restantes = 0

                cor = "🟢"
                msg_status = f"Em dia (Faltam {dias_restantes} dias)"
                bg_color = "#e6fffa"
                if dias_restantes < 0:
                    cor = "🔴"
                    msg_status = f"ATRASADA ({abs(dias_restantes)} dias)"
                    bg_color = "#ffe6e6"
                elif dias_restantes <= 7:
                    cor = "🟡"
                    msg_status = f"Próxima (Faltam {dias_restantes} dias)"
                    bg_color = "#fffbea"

                with st.container():
                    st.markdown(f"""
                    <div style="padding:10px; border-radius:5px; background-color:{bg_color}; border:1px solid #ddd; margin-bottom:10px;">
                        <h4>{cor} {p[1]} - {p[2]}</h4>
                        <p><strong>Frequência:</strong> {p[3]} dias | <strong>Última:</strong> {format_date_br(p[4])} | <strong>Próxima:</strong> {format_date_br(p[5])}</p>
                        <p>Status: {msg_status}</p>
                    </div>
                    """, unsafe_allow_html=True)

                    if st.button(f"🛠️ Gerar OS Preventiva", key=f"prev_os_{p[0]}"):
                        dt_agora = datetime.now().strftime("%d/%m/%Y %H:%M")
                        desc_os = f"[PREVENTIVA] {p[2]} - Plano #{p[0]}"

                        func_padrao = run_query("SELECT id FROM usuarios WHERE role IN ('operador', 'supervisor') LIMIT 1", return_data=True)
                        fid = func_padrao[0][0] if func_padrao else None

                        os_id = run_query("INSERT INTO os (ativo_id, funcionario_id, descricao, data_abertura, status, criado_por) VALUES (?,?,?,?,?,?)",
                                  (p[6], fid, desc_os, dt_agora, 'Aberta', st.session_state['name']))

                        if p[7] and p[7] > 0:
                            passos = run_query(f"SELECT passo FROM pop_passos WHERE pop_id={p[7]}", return_data=True)
                            for passo in passos:
                                run_query("INSERT INTO os_checklist (os_id, passo, concluido) VALUES (?, ?, 0)", (os_id, passo[0]))

                        nova_ultima = hoje.strftime("%Y-%m-%d")
                        nova_proxima = (hoje + timedelta(days=p[3])).strftime("%Y-%m-%d")
                        run_query(f"UPDATE preventiva SET ultima_execucao=?, proxima_execucao=? WHERE id=?", (nova_ultima, nova_proxima, p[0]))

                        st.success("OS Preventiva com Checklist gerada e Plano Reagendado com sucesso! Atualizando tela...")
                        st.rerun()

    with tab_nova:
        st.subheader("Gerenciar Planos")
        with st.expander("➕ Novo Plano de Manutenção"):
            ativos_db = run_query("SELECT id, nome FROM ativos WHERE ativo=1", return_data=True)
            pops_db = run_query("SELECT id, nome FROM pop_modelos", return_data=True)

            if ativos_db:
                with st.form("new_plan", clear_on_submit=True):
                    atv = st.selectbox("Ativo", ativos_db, format_func=lambda x: x[1])
                    tar = st.text_input("Tarefa (Ex: Troca de Óleo)")

                    pop_sel = None
                    if pops_db:
                        lista_pops = [(0, "Nenhum POP")] + pops_db
                        pop_sel = st.selectbox("Vincular Checklist / POP (Opcional)", lista_pops, format_func=lambda x: x[1])

                    freq = st.number_input("Frequência (Dias)", min_value=1, value=30)
                    ult = st.date_input("Data da Última Execução", format="DD/MM/YYYY")
                    if st.form_submit_button("Criar"):
                        prox = (ult + timedelta(days=freq)).strftime("%Y-%m-%d")
                        id_pop_insert = pop_sel[0] if pop_sel else 0
                        run_query("INSERT INTO preventiva (ativo_id, tarefa, frequencia_dias, ultima_execucao, proxima_execucao, pop_id) VALUES (?,?,?,?,?,?)",
                                  (atv[0], tar, freq, str(ult), prox, id_pop_insert))
                        st.success("Plano Criado!")
                        st.rerun()
            else: st.warning("Cadastre ativos primeiro.")

        st.divider()
        st.subheader("Lista de Planos Existentes")
        planos_lista = run_query("""
            SELECT p.id, a.nome, p.tarefa, p.frequencia_dias, p.ultima_execucao, p.proxima_execucao
            FROM preventiva p JOIN ativos a ON p.ativo_id = a.id
        """, return_data=True)

        if planos_lista:
            for pl in planos_lista:
                with st.expander(f"{pl[1]} - {pl[2]}"):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.write(f"**Frequência:** {pl[3]} dias | **Próxima:** {format_date_br(pl[5])}")
                        with st.form(f"edit_plan_{pl[0]}"):
                            n_tarefa = st.text_input("Tarefa", pl[2])
                            n_freq = st.number_input("Frequência", value=pl[3])
                            try: val_prox = datetime.strptime(pl[5], "%Y-%m-%d").date()
                            except: val_prox = datetime.now().date()
                            n_prox = st.date_input("Próxima Data", value=val_prox, format="DD/MM/YYYY")
                            if st.form_submit_button("Atualizar Plano"):
                                run_query("UPDATE preventiva SET tarefa=?, frequencia_dias=?, proxima_execucao=? WHERE id=?",
                                          (n_tarefa, n_freq, str(n_prox), pl[0]))
                                st.success("Atualizado!")
                                st.rerun()
                    with c2:
                        if st.button("Excluir Plano", key=f"del_plan_{pl[0]}"):
                            run_query(f"DELETE FROM preventiva WHERE id={pl[0]}")
                            st.warning("Plano removido.")
                            st.rerun()

elif menu == "Ativos":
    st.title("🚜 Ativos e Máquinas")
    tab_lista, tab_nova, tab_vinc = st.tabs(["Consultar Ficha / Editar", "Cadastrar Ativo", "Vincular Peças"])

    with tab_lista:
        if 'page_ativos' not in st.session_state: st.session_state.page_ativos = 1

        col_search, col_chk = st.columns([3, 1])
        termo = col_search.text_input("🔍 Buscar Ativo", placeholder="Nome ou Setor...")
        mostrar_inativos = col_chk.checkbox("Mostrar ativos ocultos (inativos)", value=False)

        q_count = "SELECT count(*) FROM ativos WHERE 1=1"
        params_count = []
        if not mostrar_inativos: q_count += " AND ativo=1"
        if termo:
            p = sql_like(termo)
            q_count += " AND (nome LIKE ? ESCAPE '\\' OR setor LIKE ? ESCAPE '\\')"
            params_count.extend([p, p])

        total_items = run_query(q_count, tuple(params_count), return_data=True)[0][0]
        items_por_pagina = 10
        total_pages = max(1, (total_items + items_por_pagina - 1) // items_por_pagina)

        if st.session_state.page_ativos > total_pages: st.session_state.page_ativos = total_pages
        elif st.session_state.page_ativos < 1: st.session_state.page_ativos = 1

        offset = (st.session_state.page_ativos - 1) * items_por_pagina

        q = "SELECT id, nome, setor, data_aquisicao, descricao, ativo FROM ativos WHERE 1=1"
        params_q = []
        if not mostrar_inativos: q += " AND ativo=1"
        if termo:
            p = sql_like(termo)
            q += " AND (nome LIKE ? ESCAPE '\\' OR setor LIKE ? ESCAPE '\\')"
            params_q.extend([p, p])
        q += f" ORDER BY ativo DESC, nome ASC LIMIT {items_por_pagina} OFFSET {offset}"

        dados = run_query(q, tuple(params_q), return_data=True)

        if dados:
            st.caption(f"Mostrando ativos da página {st.session_state.page_ativos} de {total_pages} (Total: {total_items} registros).")
            st.divider()

            for row in dados:
                aid, anome, asetor, adata, adesc, aativo = row
                aimg = get_ativo_imagem(aid)

                status_color = "🟢" if aativo == 1 else "⚫"

                with st.container(border=True):
                    c_img, c_main = st.columns([1, 6], vertical_alignment="center")

                    with c_img:
                        if aimg:
                            try: st.image(Image.open(io.BytesIO(aimg)), use_container_width=True)
                            except: st.info("Sem foto")
                        else:
                            st.info("Sem foto")

                    with c_main:
                        status_txt = "" if aativo == 1 else " (Oculto/Inativo)"
                        st.markdown(f"#### {status_color} {anome}{status_txt}")
                        st.write(f"**Setor:** {asetor} | **Data Aquisição:** {format_date_br(adata)}")

                        with st.expander("🛠️ Ver Ficha Completa / Editar Ativo"):
                            st.write(f"**Descrição / Detalhes Técnicos:**\n{adesc}")
                            st.divider()

                            st.markdown("#### ⚙️ Peças Vinculadas (Lista Técnica)")
                            pecas_vinc = run_query(f"SELECT e.nome, e.qtd, e.minimo, e.unidade FROM ativos_pecas ap JOIN estoque e ON ap.peca_id = e.id WHERE ap.ativo_id = {aid}", return_data=True)

                            if pecas_vinc:
                                lista_pecas = []
                                for p in pecas_vinc:
                                    status = "🔴 Estoque Baixo" if p[1] < p[2] else "🟢 OK"
                                    unid_str = p[3] if p[3] else "Un"
                                    lista_pecas.append({"Nome da Peça": p[0], "Estoque Atual": f"{p[1]} {unid_str}", "Estoque Mínimo": f"{p[2]} {unid_str}", "Status": status})
                                st.dataframe(pd.DataFrame(lista_pecas), use_container_width=True)
                            else:
                                st.warning("Nenhuma peça vinculada a este ativo.")

                            st.divider()

                            st.write("✏️ **Editar Dados**")
                            with st.form(f"edit_at_{aid}"):
                                nn = st.text_input("Nome", anome)
                                try: idx_setor = ["Produção", "Manutenção", "Logística", "Administrativo", "Geral"].index(asetor)
                                except: idx_setor = 0
                                ns = st.selectbox("Setor", ["Produção", "Manutenção", "Logística", "Administrativo", "Geral"], index=idx_setor)

                                try: val_data = datetime.strptime(adata, "%Y-%m-%d").date()
                                except:
                                    try: val_data = datetime.strptime(adata, "%d/%m/%Y").date()
                                    except: val_data = datetime.now().date()

                                nd = st.date_input("Data de Aquisição", value=val_data, format="DD/MM/YYYY")
                                ndes = st.text_area("Descrição", adesc)
                                nova_foto_ativo = st.file_uploader("Alterar Foto do Ativo", type=['png', 'jpg', 'jpeg'])

                                if st.form_submit_button("Atualizar Ficha do Ativo"):
                                    if nova_foto_ativo:
                                        run_query("UPDATE ativos SET nome=?, setor=?, data_aquisicao=?, descricao=?, imagem=? WHERE id=?", (nn,ns,str(nd),ndes,otimizar_imagem(nova_foto_ativo),aid))
                                    else:
                                        run_query("UPDATE ativos SET nome=?, setor=?, data_aquisicao=?, descricao=? WHERE id=?", (nn,ns,str(nd),ndes,aid))
                                    st.success("Ficha atualizada com sucesso!")
                                    st.rerun()

                            st.divider()
                            st.write("⚠️ **Ações Avançadas**")
                            c_btn1, c_btn2 = st.columns(2)

                            if aativo == 1:
                                if c_btn1.button("👁️ Ocultar Máquina (Inativar)", key=f"hide_at_{aid}", help="A máquina não vai aparecer mais para o técnico inserir na OS, mas o histórico é mantido."):
                                    run_query(f"UPDATE ativos SET ativo=0 WHERE id={aid}")
                                    st.rerun()
                            else:
                                if c_btn1.button("👁️ Reativar Máquina", key=f"show_at_{aid}"):
                                    run_query(f"UPDATE ativos SET ativo=1 WHERE id={aid}")
                                    st.rerun()

                            if c_btn2.button("🗑️ Excluir Definitivamente", key=f"del_at_{aid}", type="primary", help="Cuidado! Excluir apagará todas as dependências desta máquina."):
                                run_query(f"DELETE FROM ativos_pecas WHERE ativo_id={aid}")
                                run_query(f"DELETE FROM preventiva WHERE ativo_id={aid}")
                                run_query(f"DELETE FROM ativos WHERE id={aid}")
                                st.rerun()

            st.divider()
            c_prev, c_page, c_next = st.columns([1, 2, 1])
            if c_prev.button("⬅️ Página Anterior", key="prev_atv", disabled=(st.session_state.page_ativos == 1)):
                st.session_state.page_ativos -= 1
                st.rerun()
            with c_page:
                st.markdown(f"<div style='text-align: center;'><b>Página {st.session_state.page_ativos} de {total_pages}</b></div>", unsafe_allow_html=True)
            if c_next.button("Próxima Página ➡️", key="next_atv", disabled=(st.session_state.page_ativos == total_pages)):
                st.session_state.page_ativos += 1
                st.rerun()

        else:
            st.info("Nenhum ativo encontrado para esta pesquisa.")

        st.divider()
        with st.expander("📥 Exportar Ativos Completos para Excel"):
            df_export_q = "SELECT id, nome, setor, data_aquisicao, descricao, ativo FROM ativos"
            df_export_data = run_query(df_export_q, return_data=True)
            if df_export_data:
                df = pd.DataFrame(df_export_data, columns=["ID", "Nome", "Setor", "Data", "Descrição", "Ativo (1=Sim, 0=Não)"])
                st.download_button("Baixar Planilha Excel", converter_df_para_excel(df), "ativos_completo.xlsx")

    with tab_nova:
        with st.form("form_ativo", clear_on_submit=True):
            nome = st.text_input("Nome da Máquina")
            setor = st.selectbox("Setor", ["Produção", "Manutenção", "Logística", "Administrativo", "Geral"])
            data = st.date_input("Data Aquisição", format="DD/MM/YYYY")
            desc = st.text_area("Descrição / Detalhes Técnicos")
            foto_ativo = st.file_uploader("Foto do Ativo (Opcional)", type=['png', 'jpg', 'jpeg'])

            if st.form_submit_button("Salvar Novo Ativo"):
                blob_ativo = otimizar_imagem(foto_ativo) if foto_ativo else None
                run_query("INSERT INTO ativos (nome, setor, data_aquisicao, descricao, imagem, ativo) VALUES (?,?,?,?,?,?)",
                          (nome, setor, str(data), desc, blob_ativo, 1))
                st.success("Salvo com sucesso!")
                st.rerun()

    with tab_vinc:
        st.subheader("🔗 Associar Peças ao Ativo")
        termo_vinc = st.text_input("Filtrar Ativos para Vinculo", placeholder="Nome da máquina...")
        q_ativos = "SELECT id, nome FROM ativos WHERE ativo=1"
        params_ativos = []
        if termo_vinc:
            q_ativos += " AND nome LIKE ? ESCAPE '\\'"
            params_ativos.append(sql_like(termo_vinc))
        ativos_db = run_query(q_ativos, tuple(params_ativos), return_data=True)
        pecas_db = run_query("SELECT id, nome FROM estoque WHERE ativo=1", return_data=True)
        if ativos_db and pecas_db:
            c1, c2, c3 = st.columns([2,2,1])
            sel_ativo = c1.selectbox("Ativo", ativos_db, format_func=lambda x: x[1])
            sel_peca = c2.selectbox("Peça", pecas_db, format_func=lambda x: x[1])

            c3.markdown("<br>", unsafe_allow_html=True)
            if c3.button("Vincular Peça ao Ativo", use_container_width=True):
                exists = run_query(f"SELECT count(*) FROM ativos_pecas WHERE ativo_id={sel_ativo[0]} AND peca_id={sel_peca[0]}", return_data=True)[0][0]
                if exists == 0:
                    run_query("INSERT INTO ativos_pecas (ativo_id, peca_id) VALUES (?,?)", (sel_ativo[0], sel_peca[0]))
                    st.success("Vinculado!")
                    st.rerun()
                else:
                    st.warning("Peça já vinculada.")

            st.divider()
            st.write(f"**Peças instaladas na máquina selecionada acima:**")
            vincs = run_query(f"SELECT ap.id, e.nome FROM ativos_pecas ap JOIN estoque e ON ap.peca_id=e.id WHERE ap.ativo_id={sel_ativo[0]}", return_data=True)
            if vincs:
                for v in vincs:
                    c_a, c_b = st.columns([4,1])
                    c_a.write(f"- {v[1]}")
                    if c_b.button("🗑️ Remover", key=f"d_atv_{v[0]}"):
                        run_query(f"DELETE FROM ativos_pecas WHERE id={v[0]}")
                        st.rerun()

elif menu == "Estoque":
    st.title("📦 Estoque e Peças")
    tab_lista, tab_nova, tab_vinc = st.tabs(["Consultar Ficha / Editar", "Cadastrar Nova Peça", "Vincular Fornecedores"])

    with tab_lista:
        if 'page_estoque' not in st.session_state: st.session_state.page_estoque = 1

        col_search, col_chk = st.columns([3, 1])
        termo = col_search.text_input("🔍 Buscar Peça", placeholder="Nome da peça...")
        mostrar_inativos = col_chk.checkbox("Mostrar peças ocultas (inativas)", value=False)

        q_count = "SELECT count(*) FROM estoque WHERE 1=1"
        params_count = []
        if not mostrar_inativos: q_count += " AND ativo=1"
        if termo:
            q_count += " AND nome LIKE ? ESCAPE '\\'"
            params_count.append(sql_like(termo))

        total_items = run_query(q_count, tuple(params_count), return_data=True)[0][0]
        items_por_pagina = 10
        total_pages = max(1, (total_items + items_por_pagina - 1) // items_por_pagina)

        if st.session_state.page_estoque > total_pages: st.session_state.page_estoque = total_pages
        elif st.session_state.page_estoque < 1: st.session_state.page_estoque = 1

        offset = (st.session_state.page_estoque - 1) * items_por_pagina

        q = "SELECT id, nome, qtd, minimo, preco, unidade, ativo FROM estoque WHERE 1=1"
        params_q = []
        if not mostrar_inativos: q += " AND ativo=1"
        if termo:
            q += " AND nome LIKE ? ESCAPE '\\'"
            params_q.append(sql_like(termo))
        q += f" ORDER BY ativo DESC, nome ASC LIMIT {items_por_pagina} OFFSET {offset}"

        dados = run_query(q, tuple(params_q), return_data=True)

        if dados:
            st.caption(f"Mostrando peças da página {st.session_state.page_estoque} de {total_pages} (Total: {total_items} registros).")
            st.divider()

            for row in dados:
                pid, pnome, pqtd, pmin, ppreco, punid, pativo = row
                pimg = get_estoque_imagem(pid)
                punid_str = punid if punid else "Un"

                status_color = "🟢" if pqtd >= pmin else "🔴"
                if pativo == 0: status_color = "⚫"

                with st.container(border=True):
                    c_img, c_main = st.columns([1, 6], vertical_alignment="center")

                    with c_img:
                        if pimg:
                            try: st.image(Image.open(io.BytesIO(pimg)), use_container_width=True)
                            except: st.info("Sem foto")
                        else:
                            st.info("Sem foto")

                    with c_main:
                        if pativo == 0: st.markdown(f"#### {status_color} {pnome} (Oculta/Inativa)")
                        else: st.markdown(f"#### {status_color} {pnome}")

                        st.write(f"**Estoque:** {pqtd} {punid_str} | **Mínimo:** {pmin} {punid_str} | **Preço Est.:** R$ {ppreco:.2f}")

                        with st.expander("🛠️ Ver Ficha Completa / Editar Peça"):

                            col_maq, col_forn = st.columns(2)
                            with col_maq:
                                st.write("⚙️ **Máquinas que utilizam**")
                                maquinas_vinc = run_query(f"SELECT a.nome, a.setor FROM ativos_pecas ap JOIN ativos a ON ap.ativo_id = a.id WHERE ap.peca_id = {pid}", return_data=True)
                                if maquinas_vinc:
                                    st.dataframe(pd.DataFrame(maquinas_vinc, columns=["Máquina", "Setor"]), use_container_width=True)
                                else:
                                    st.caption("Nenhuma máquina vinculada.")

                            with col_forn:
                                st.write("🚚 **Fornecedores desta peça**")
                                fornecedores_vinc = run_query(f"SELECT f.id, f.nome, f.telefone FROM estoque_fornecedores ef JOIN fornecedores f ON ef.fornecedor_id = f.id WHERE ef.peca_id = {pid}", return_data=True)
                                if fornecedores_vinc:
                                    for f_vinc in fornecedores_vinc:
                                        c_fn, c_fb = st.columns([2, 1])
                                        c_fn.write(f"- **{f_vinc[1]}**")
                                        with c_fb.popover("🛒 Criar OC"):
                                            def_qtd = max(1, pmin - pqtd)
                                            with st.form(f"f_oc_{pid}_{f_vinc[0]}", clear_on_submit=True):
                                                qtd_pedida = st.number_input("Quantidade:", min_value=1, value=def_qtd)
                                                if st.form_submit_button("Gerar Pedido"):
                                                    tot = qtd_pedida * ppreco
                                                    dt_hoje = datetime.now().strftime("%d/%m/%Y")
                                                    cid = run_query("INSERT INTO compras (fornecedor_id, data_pedido, status, total_estimado, tipo, criado_por) VALUES (?,?,?,?,?,?)",
                                                                    (f_vinc[0], dt_hoje, 'Pendente', tot, 'Padrão', st.session_state['name']))
                                                    run_query("INSERT INTO compras_itens (compra_id, peca_id, qtd_pedida, preco_unitario) VALUES (?,?,?,?)",
                                                              (cid, pid, qtd_pedida, ppreco))
                                                    st.success(f"OC #{cid} gerada! Acesse a aba 'Compras'.")
                                else:
                                    st.caption("Nenhum fornecedor vinculado.")

                            st.divider()
                            st.write("✏️ **Editar Dados**")
                            with st.form(f"edit_peca_{pid}"):
                                n_nome = st.text_input("Nome", pnome)
                                c1, c2, c3, c4 = st.columns(4)
                                n_qtd = c1.number_input("Qtd Atual", value=int(pqtd))
                                n_min = c2.number_input("Mínimo", value=int(pmin))
                                n_preco = c3.number_input("Preço Unit. (R$)", value=float(ppreco))

                                lista_unidades = ["Un", "Kg", "Metros", "Litros", "Pares", "Caixa", "M²", "Serviço"]
                                idx_un = lista_unidades.index(punid_str) if punid_str in lista_unidades else 0
                                n_unidade = c4.selectbox("Unidade", lista_unidades, index=idx_un)

                                nova_foto = st.file_uploader("Alterar Foto", type=['png', 'jpg', 'jpeg'])

                                if st.form_submit_button("Atualizar Ficha"):
                                    if nova_foto:
                                        run_query("UPDATE estoque SET nome=?, qtd=?, minimo=?, preco=?, imagem=?, unidade=? WHERE id=?",
                                                  (n_nome, n_qtd, n_min, n_preco, otimizar_imagem(nova_foto), n_unidade, pid))
                                    else:
                                        run_query("UPDATE estoque SET nome=?, qtd=?, minimo=?, preco=?, unidade=? WHERE id=?",
                                                  (n_nome, n_qtd, n_min, n_preco, n_unidade, pid))
                                    st.success("Peça atualizada!")
                                    st.rerun()

                            st.divider()
                            st.write("⚠️ **Ações Avançadas**")
                            c_btn1, c_btn2 = st.columns(2)

                            if pativo == 1:
                                if c_btn1.button("👁️ Ocultar Peça (Inativar)", key=f"hide_{pid}", help="A peça não vai aparecer mais para o técnico inserir na OS, mas o histórico é mantido."):
                                    run_query(f"UPDATE estoque SET ativo=0 WHERE id={pid}")
                                    st.rerun()
                            else:
                                if c_btn1.button("👁️ Reativar Peça", key=f"show_{pid}"):
                                    run_query(f"UPDATE estoque SET ativo=1 WHERE id={pid}")
                                    st.rerun()

                            if c_btn2.button("🗑️ Excluir Definitivamente", key=f"del_{pid}", type="primary"):
                                run_query(f"DELETE FROM estoque_fornecedores WHERE peca_id={pid}")
                                run_query(f"DELETE FROM ativos_pecas WHERE peca_id={pid}")
                                run_query(f"DELETE FROM estoque WHERE id={pid}")
                                st.rerun()

            st.divider()
            c_prev, c_page, c_next = st.columns([1, 2, 1])
            if c_prev.button("⬅️ Página Anterior", key="prev_est", disabled=(st.session_state.page_estoque == 1)):
                st.session_state.page_estoque -= 1
                st.rerun()
            with c_page:
                st.markdown(f"<div style='text-align: center;'><b>Página {st.session_state.page_estoque} de {total_pages}</b></div>", unsafe_allow_html=True)
            if c_next.button("Próxima Página ➡️", key="next_est", disabled=(st.session_state.page_estoque == total_pages)):
                st.session_state.page_estoque += 1
                st.rerun()

        else:
            st.info("Nenhuma peça encontrada para esta pesquisa.")

        st.divider()
        with st.expander("📥 Exportar Estoque Completo para Excel"):
            df_export_q = "SELECT id, nome, qtd, minimo, preco, unidade, ativo FROM estoque"
            df_export_data = run_query(df_export_q, return_data=True)
            if df_export_data:
                df = pd.DataFrame(df_export_data, columns=["ID", "Nome", "Qtd", "Mínimo", "Preço", "Unidade", "Ativo (1=Sim, 0=Não)"])
                st.download_button("Baixar Planilha Excel", converter_df_para_excel(df), "estoque_completo.xlsx")

    with tab_nova:
        with st.form("form_estoque", clear_on_submit=True):
            nome = st.text_input("Nome da Peça")
            c1, c2, c3, c4 = st.columns(4)
            qtd = c1.number_input("Qtd Atual", min_value=0)
            minimo = c2.number_input("Estoque Mínimo", min_value=0)
            preco = c3.number_input("Preço Estimado (R$)", min_value=0.0)

            unidade = c4.selectbox("Unidade", ["Un", "Kg", "Metros", "Litros", "Pares", "Caixa", "M²", "Serviço"])
            foto_peca = st.file_uploader("Foto da Peça (Opcional)", type=['png', 'jpg', 'jpeg'])

            if st.form_submit_button("Salvar Nova Peça"):
                nome_limpo = nome.strip()
                if nome_limpo:
                    # VERIFICAÇÃO DE DUPLICIDADE
                    ja_existe = run_query("SELECT count(*) FROM estoque WHERE LOWER(nome) = LOWER(?)", (nome_limpo,), return_data=True)[0][0]

                    if ja_existe > 0:
                        st.error(f"⚠️ A peça '{nome_limpo}' já está cadastrada no sistema! Busque na aba ao lado.")
                    else:
                        blob_peca = otimizar_imagem(foto_peca) if foto_peca else None
                        run_query("INSERT INTO estoque (nome, qtd, minimo, preco, imagem, unidade, ativo) VALUES (?,?,?,?,?,?,?)",
                                  (nome_limpo, qtd, minimo, preco, blob_peca, unidade, 1))
                        st.success("Peça cadastrada com sucesso!")
                        st.rerun()
                else:
                    st.error("O nome da peça é obrigatório.")

    with tab_vinc:
        st.subheader("🔗 Associar Fornecedores à Peça")
        pecas_db = run_query("SELECT id, nome FROM estoque WHERE ativo=1", return_data=True)
        forns_db = run_query("SELECT id, nome FROM fornecedores WHERE ativo=1", return_data=True)

        if pecas_db and forns_db:
            col1, col2, col3 = st.columns([2, 2, 1])
            sel_peca_f = col1.selectbox("Selecione a Peça", pecas_db, format_func=lambda x: x[1])
            sel_forn = col2.selectbox("Selecione o Fornecedor", forns_db, format_func=lambda x: x[1])

            col3.markdown("<br>", unsafe_allow_html=True)
            if col3.button("Vincular Fornecedor", use_container_width=True):
                exists = run_query(f"SELECT count(*) FROM estoque_fornecedores WHERE peca_id={sel_peca_f[0]} AND fornecedor_id={sel_forn[0]}", return_data=True)[0][0]
                if exists == 0:
                    run_query("INSERT INTO estoque_fornecedores (peca_id, fornecedor_id) VALUES (?,?)", (sel_peca_f[0], sel_forn[0]))
                    st.success("Fornecedor vinculado à peça!")
                    st.rerun()
                else:
                    st.warning("Este fornecedor já está vinculado a esta peça.")

            st.divider()
            st.write(f"**Fornecedores da peça selecionada:**")
            vincs_f = run_query(f"SELECT ef.id, f.nome, f.telefone FROM estoque_fornecedores ef JOIN fornecedores f ON ef.fornecedor_id=f.id WHERE ef.peca_id={sel_peca_f[0]}", return_data=True)
            if vincs_f:
                for v in vincs_f:
                    c_a, c_b = st.columns([4, 1])
                    c_a.write(f"- {v[1]} (Tel: {v[2]})")
                    if c_b.button("🗑️ Remover", key=f"d_forn_{v[0]}"):
                        run_query(f"DELETE FROM estoque_fornecedores WHERE id={v[0]}")
                        st.rerun()
        else:
            st.warning("Você precisa ter pelo menos uma peça e um fornecedor cadastrados.")

elif menu == "Fornecedores":
    st.title("🚚 Gestão de Fornecedores")

    tab_lista, tab_nova, tab_cat = st.tabs(["Consultar Ficha / Editar", "Cadastrar Novo", "Gerenciar Categorias"])

    with tab_lista:
        if 'page_forn' not in st.session_state: st.session_state.page_forn = 1

        col_search, col_cat, col_chk = st.columns([2, 1, 1])
        termo = col_search.text_input("🔍 Buscar Fornecedor", placeholder="Nome ou CNPJ...")

        cat_data = run_query("SELECT DISTINCT nome FROM categorias_fornecedores ORDER BY nome", return_data=True)
        cat_lista = ["Todas"] + [c[0] for c in cat_data] if cat_data else ["Todas"]
        filtro_cat = col_cat.selectbox("🏷️ Categoria", cat_lista)

        mostrar_inativos = col_chk.checkbox("Mostrar ocultos (inativos)", value=False)

        q_count = "SELECT count(*) FROM fornecedores WHERE 1=1"
        params_count = []
        if not mostrar_inativos: q_count += " AND ativo=1"
        if termo:
            p = sql_like(termo)
            q_count += " AND (nome LIKE ? ESCAPE '\\' OR cnpj LIKE ? ESCAPE '\\')"
            params_count.extend([p, p])
        if filtro_cat != "Todas":
            q_count += " AND categoria=?"
            params_count.append(filtro_cat)

        total_items = run_query(q_count, tuple(params_count), return_data=True)[0][0]
        items_por_pagina = 10
        total_pages = max(1, (total_items + items_por_pagina - 1) // items_por_pagina)

        if st.session_state.page_forn > total_pages: st.session_state.page_forn = total_pages
        elif st.session_state.page_forn < 1: st.session_state.page_forn = 1

        offset = (st.session_state.page_forn - 1) * items_por_pagina

        q = "SELECT id, nome, contato, telefone, email, cnpj, endereco, cidade, estado, cep, ativo, categoria, apresentacao FROM fornecedores WHERE 1=1"
        params_q = []
        if not mostrar_inativos: q += " AND ativo=1"
        if termo:
            p = sql_like(termo)
            q += " AND (nome LIKE ? ESCAPE '\\' OR cnpj LIKE ? ESCAPE '\\')"
            params_q.extend([p, p])
        if filtro_cat != "Todas":
            q += " AND categoria=?"
            params_q.append(filtro_cat)
        q += f" ORDER BY ativo DESC, nome ASC LIMIT {items_por_pagina} OFFSET {offset}"

        dados = run_query(q, tuple(params_q), return_data=True)

        if dados:
            st.caption(f"Mostrando fornecedores da página {st.session_state.page_forn} de {total_pages} (Total: {total_items} registros).")
            st.divider()

            for row in dados:
                fid, fnome, fcontato, ftel, femail, fcnpj, fend, fcid, fest, fcep, fativo, fcategoria, fapres = row

                status_color = "🟢" if fativo == 1 else "⚫"

                with st.container(border=True):
                    c_icon, c_main = st.columns([1, 10], vertical_alignment="center")

                    with c_icon:
                        st.markdown("<h1 style='text-align: center;'>🏢</h1>", unsafe_allow_html=True)

                    with c_main:
                        status_txt = "" if fativo == 1 else " (Oculto/Inativo)"
                        st.markdown(f"#### {status_color} {fnome}{status_txt}")
                        st.write(f"**Categoria:** {fcategoria or 'Não definida'} | **CNPJ:** {fcnpj} | **Contato:** {fcontato} | **Tel:** {ftel}")

                        if fapres:
                            st.info(f"**Sobre:** {fapres}")

                        with st.expander("🛠️ Ver Ficha Completa / Editar Fornecedor"):
                            with st.form(f"edit_forn_{fid}"):
                                st.write("Dados Principais")
                                c1, c2, c_cat_edit = st.columns([2, 1, 1])
                                n_nome = c1.text_input("Nome", fnome)
                                n_cnpj = c2.text_input("CNPJ", fcnpj or "")

                                cat_opcoes = [c[1] for c in run_query("SELECT id, nome FROM categorias_fornecedores ORDER BY nome", return_data=True)]
                                if not cat_opcoes: cat_opcoes = ["Geral"]
                                if fcategoria not in cat_opcoes and fcategoria:
                                    cat_opcoes.insert(0, fcategoria)

                                idx_cat = cat_opcoes.index(fcategoria) if fcategoria in cat_opcoes else 0
                                n_categoria = c_cat_edit.selectbox("Categoria", cat_opcoes, index=idx_cat)

                                n_apres = st.text_area("Breve Apresentação / Observações (Produtos, serviços oferecidos)", fapres or "")

                                st.write("Contato")
                                c3, c4, c5 = st.columns(3)
                                n_contato = c3.text_input("Contato", fcontato or "")
                                n_tel = c4.text_input("Telefone", ftel or "")
                                n_email = c5.text_input("E-mail", femail or "")

                                st.write("Endereço")
                                n_end = st.text_input("Endereço", fend or "")
                                c6, c7, c8 = st.columns([2, 1, 1])
                                n_cid = c6.text_input("Cidade", fcid or "")
                                n_est = c7.text_input("Estado (UF)", fest or "")
                                n_cep = c8.text_input("CEP", fcep or "")

                                if st.form_submit_button("Atualizar Dados"):
                                    run_query("UPDATE fornecedores SET nome=?, contato=?, telefone=?, email=?, cnpj=?, endereco=?, cidade=?, estado=?, cep=?, categoria=?, apresentacao=? WHERE id=?",
                                              (n_nome, n_contato, n_tel, n_email, n_cnpj, n_end, n_cid, n_est, n_cep, n_categoria, n_apres, fid))
                                    st.success("Fornecedor atualizado!")
                                    st.rerun()

                            st.divider()
                            st.write("⚠️ **Ações Avançadas**")
                            c_btn1, c_btn2 = st.columns(2)

                            if fativo == 1:
                                if c_btn1.button("👁️ Ocultar Fornecedor (Inativar)", key=f"hide_forn_{fid}"):
                                    run_query(f"UPDATE fornecedores SET ativo=0 WHERE id={fid}")
                                    st.rerun()
                            else:
                                if c_btn1.button("👁️ Reativar Fornecedor", key=f"show_forn_{fid}"):
                                    run_query(f"UPDATE fornecedores SET ativo=1 WHERE id={fid}")
                                    st.rerun()

                            if c_btn2.button("🗑️ Excluir Definitivamente", key=f"del_forn_{fid}", type="primary"):
                                run_query(f"DELETE FROM estoque_fornecedores WHERE fornecedor_id={fid}")
                                run_query(f"DELETE FROM fornecedores WHERE id={fid}")
                                st.rerun()

            st.divider()
            c_prev, c_page, c_next = st.columns([1, 2, 1])
            if c_prev.button("⬅️ Página Anterior", key="prev_forn", disabled=(st.session_state.page_forn == 1)):
                st.session_state.page_forn -= 1
                st.rerun()
            with c_page:
                st.markdown(f"<div style='text-align: center;'><b>Página {st.session_state.page_forn} de {total_pages}</b></div>", unsafe_allow_html=True)
            if c_next.button("Próxima Página ➡️", key="next_forn", disabled=(st.session_state.page_forn == total_pages)):
                st.session_state.page_forn += 1
                st.rerun()
        else:
            st.info("Nenhum fornecedor encontrado para esta pesquisa.")

        st.divider()
        with st.expander("📥 Exportar Fornecedores para Excel"):
            df_export_q = "SELECT id, nome, categoria, contato, telefone, email, cnpj, cidade, ativo, apresentacao FROM fornecedores"
            df_export_data = run_query(df_export_q, return_data=True)
            if df_export_data:
                df = pd.DataFrame(df_export_data, columns=["ID", "Nome", "Categoria", "Contato", "Telefone", "Email", "CNPJ", "Cidade", "Ativo (1=Sim, 0=Não)", "Apresentação"])
                st.download_button("Baixar Planilha Excel", converter_df_para_excel(df), "fornecedores_completo.xlsx")

    with tab_nova:
        with st.form("form_forn", clear_on_submit=True):
            st.subheader("Dados Principais")
            c1, c2, c_cat_add = st.columns([2, 1, 1])
            f_nome = c1.text_input("Razão Social / Nome Fantasia *")
            f_cnpj = c2.text_input("CNPJ / NIF")

            cat_opcoes_add = [c[1] for c in run_query("SELECT id, nome FROM categorias_fornecedores ORDER BY nome", return_data=True)]
            if not cat_opcoes_add: cat_opcoes_add = ["Geral"]
            f_categoria = c_cat_add.selectbox("Categoria", cat_opcoes_add)

            f_apres = st.text_area("Breve Apresentação (Ex: Especialista em rolamentos de alta rotação. Atende chamados emergenciais)")

            st.subheader("Contato")
            c3, c4, c5 = st.columns(3)
            f_contato = c3.text_input("Pessoa de Contato")
            f_tel = c4.text_input("Telefone / WhatsApp (Apenas Números)")
            f_email = c5.text_input("E-mail Comercial")

            st.subheader("Endereço")
            f_end = st.text_input("Logradouro (Rua, Número, Bairro)")
            c6, c7, c8 = st.columns([2, 1, 1])
            f_cid = c6.text_input("Cidade")
            f_est = c7.text_input("Estado (UF)")
            f_cep = c8.text_input("CEP")

            if st.form_submit_button("Salvar Novo Fornecedor"):
                nome_limpo = f_nome.strip()
                cnpj_limpo = f_cnpj.strip()

                if nome_limpo:
                    # VERIFICAÇÃO DE DUPLICIDADE (Por Nome ou CNPJ)
                    query_check = "SELECT count(*) FROM fornecedores WHERE LOWER(nome) = LOWER(?)"
                    params_check = [nome_limpo]

                    if cnpj_limpo:
                        query_check += " OR cnpj = ?"
                        params_check.append(cnpj_limpo)

                    ja_existe = run_query(query_check, tuple(params_check), return_data=True)[0][0]

                    if ja_existe > 0:
                        st.error("⚠️ Atenção: Já existe um fornecedor cadastrado com este Nome ou este CNPJ!")
                    else:
                        run_query("INSERT INTO fornecedores (nome, contato, telefone, email, cnpj, endereco, cidade, estado, cep, ativo, categoria, apresentacao) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (nome_limpo, f_contato, f_tel, f_email, cnpj_limpo, f_end, f_cid, f_est, f_cep, 1, f_categoria, f_apres))
                        st.success("Fornecedor cadastrado com sucesso!")
                        st.rerun()
                else:
                    st.error("O Nome/Razão Social é obrigatório.")

    with tab_cat:
        st.subheader("🏷️ Gerenciar Categorias")
        st.write("Adicione ou remova ramos de atuação para organizar a sua base de fornecedores.")

        with st.form("nova_categoria_form", clear_on_submit=True):
            cx1, cx2 = st.columns([3, 1])
            nova_cat = cx1.text_input("Nome da Nova Categoria (Ex: EPIs, Serviços de Usinagem, Componentes Elétricos)")
            if cx2.form_submit_button("➕ Adicionar Categoria"):
                if nova_cat:
                    run_query("INSERT OR IGNORE INTO categorias_fornecedores (nome) VALUES (?)", (nova_cat.strip(),))
                    st.success("Categoria adicionada com sucesso!")
                    st.rerun()

        st.divider()
        st.write("**Categorias Cadastradas:**")
        categorias = run_query("SELECT id, nome FROM categorias_fornecedores ORDER BY nome", return_data=True)
        if categorias:
            for cat in categorias:
                col_txt, col_btn = st.columns([4, 1])
                col_txt.write(f"🏷️ **{cat[1]}**")
                if col_btn.button("🗑️ Excluir", key=f"del_cat_{cat[0]}"):
                    run_query(f"DELETE FROM categorias_fornecedores WHERE id={cat[0]}")
                    st.rerun()
        else:
            st.info("Nenhuma categoria cadastrada.")

elif menu == "Config. Empresa":
    st.title("🏢 Dados da Empresa")
    curr = get_empresa()

    with st.form("fe", clear_on_submit=False):
        st.subheader("Informações Fiscais")
        c1, c2 = st.columns([3, 2])
        n = c1.text_input("Nome / Razão Social", curr[0] or "")
        cnpj = c2.text_input("CNPJ", curr[5] or "")

        st.subheader("Contato")
        c3, c4 = st.columns(2)
        t = c3.text_input("Telefone", curr[2] or "")
        em = c4.text_input("Email Oficial", curr[3] or "")

        st.subheader("Endereço Físico")
        e = st.text_input("Logradouro (Rua, Nº, Bairro)", curr[1] or "")
        c5, c6, c7 = st.columns([2, 1, 1])
        cid = c5.text_input("Cidade", curr[6] or "")
        est = c6.text_input("Estado (UF)", curr[7] or "")
        cep = c7.text_input("CEP", curr[8] or "")

        st.subheader("Identidade Visual")
        if curr[4]:
            st.write("Logo atual:")
            try: st.image(Image.open(io.BytesIO(curr[4])), width=150)
            except: pass
        logo = st.file_uploader("Logótipo da Empresa (Sairá nos PDFs)", type=['png', 'jpg', 'jpeg'])

        if st.form_submit_button("Salvar Configurações"):
            if logo:
                blob = otimizar_imagem(logo, max_size=(400, 400))
                run_query("UPDATE empresa SET nome=?, endereco=?, telefone=?, email=?, logo=?, cnpj=?, cidade=?, estado=?, cep=? WHERE id=1",
                          (n, e, t, em, blob, cnpj, cid, est, cep))
            else:
                run_query("UPDATE empresa SET nome=?, endereco=?, telefone=?, email=?, cnpj=?, cidade=?, estado=?, cep=? WHERE id=1",
                          (n, e, t, em, cnpj, cid, est, cep))
            invalidate_read_caches()
            st.success("Dados da empresa atualizados com sucesso!")
            st.rerun()

elif menu == "Ordens de Compra (OC)":
    st.title("🛒 Compras")
    tab_lista, tab_nova = st.tabs(["Gerenciar / Histórico", "Emitir Nova Ordem"])

    with tab_lista:
        st.subheader("Histórico de Compras e Serviços")

        c_filt, c_search, c_date1, c_date2 = st.columns([1, 1, 1, 1])
        filtro = c_filt.radio("Status:", ["Pendentes", "Concluídos/Recebidos", "Todas"])
        termo_oc = c_search.text_input("🔍 Buscar OC", placeholder="Nome Fornecedor ou ID...")
        dt_ini_oc = c_date1.date_input("Data De:", datetime.now().date() - timedelta(days=90), key="dt1_oc", format="DD/MM/YYYY")
        dt_fim_oc = c_date2.date_input("Data Até:", datetime.now().date() - timedelta(days=-90), key="dt2_oc", format="DD/MM/YYYY")

        q = """
            SELECT c.id, IFNULL(f.nome, 'A Definir / Excluído'), c.data_pedido, c.status, c.total_estimado, c.tipo, f.telefone, c.criado_por, c.editado_por
            FROM compras c
            LEFT JOIN fornecedores f ON c.fornecedor_id=f.id
            WHERE 1=1
        """
        if filtro == "Pendentes": q += " AND c.status='Pendente'"
        elif filtro == "Concluídos/Recebidos": q += " AND c.status IN ('Recebido', 'Concluída')"
        params_oc = []
        if termo_oc:
            p = sql_like(termo_oc)
            q += " AND (IFNULL(f.nome,'') LIKE ? ESCAPE '\\' OR CAST(c.id AS TEXT) LIKE ? ESCAPE '\\')"
            params_oc.extend([p, p])
        q += " ORDER BY c.id DESC"

        dados_compras_raw = run_query(q, tuple(params_oc), return_data=True)

        dados_compras_filtrados = []
        if dados_compras_raw:
            for d in dados_compras_raw:
                try:
                    d_obj = datetime.strptime(d[2], "%d/%m/%Y").date()
                    if dt_ini_oc <= d_obj <= dt_fim_oc:
                        dados_compras_filtrados.append(d)
                except:
                    dados_compras_filtrados.append(d)

        if dados_compras_filtrados:
            df_data_excel = [[d[0], d[1], d[2], d[3], d[4], d[5], d[7], d[8]] for d in dados_compras_filtrados]
            df_excel = pd.DataFrame(df_data_excel, columns=["ID", "Fornecedor", "Data", "Status", "Total", "Tipo", "Criado Por", "Editado Por"])
            st.download_button("📥 Baixar Relatório (Excel)", converter_df_para_excel(df_excel), "relatorio_compras.xlsx")
            st.divider()

            if 'page_oc' not in st.session_state:
                st.session_state.page_oc = 1
            items_por_pagina_oc = 10
            total_oc = len(dados_compras_filtrados)
            total_pages_oc = max(1, (total_oc + items_por_pagina_oc - 1) // items_por_pagina_oc)
            if st.session_state.page_oc > total_pages_oc:
                st.session_state.page_oc = total_pages_oc
            elif st.session_state.page_oc < 1:
                st.session_state.page_oc = 1
            offset_oc = (st.session_state.page_oc - 1) * items_por_pagina_oc
            pagina_oc = dados_compras_filtrados[offset_oc:offset_oc + items_por_pagina_oc]
            st.caption(f"Página {st.session_state.page_oc} de {total_pages_oc} ({total_oc} ordens no filtro).")
            pc1, pc2, pc3 = st.columns([1, 2, 1])
            if pc1.button("⬅️ Anterior OC", key="pag_oc_prev", disabled=st.session_state.page_oc <= 1):
                st.session_state.page_oc -= 1
                st.rerun()
            if pc3.button("Próxima OC ➡️", key="pag_oc_next", disabled=st.session_state.page_oc >= total_pages_oc):
                st.session_state.page_oc += 1
                st.rerun()

            forns_para_edicao = listar_fornecedores_ativos()

            for oc in pagina_oc:
                tipo_txt = str(oc[5]) if oc[5] else "Padrão"
                mostrar_preco = tipo_txt in ["Padrão", "Autorização de Serviço", "Reparo"]

                if "Cotação" in tipo_txt: icone = "📝"
                elif "Emergencial" in tipo_txt: icone = "🚨"
                elif "Autorização" in tipo_txt: icone = "🛠️"
                elif "Reparo" in tipo_txt: icone = "🔧"
                else: icone = "🛒"

                with st.expander(f"{icone} #{oc[0]} - {oc[1]} ({tipo_txt})", expanded=False):

                    st.caption(f"👤 **Criado por:** {oc[7] or 'N/A'}" + (f" | ✏️ **Histórico:** {oc[8]}" if oc[8] else ""))

                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.write(f"**Data:** {oc[2]} | **Status:** {oc[3]}")

                    itens_oc = run_query(f"SELECT e.nome, i.qtd_pedida, i.preco_unitario, e.unidade FROM compras_itens i LEFT JOIN estoque e ON i.peca_id = e.id WHERE i.compra_id = {oc[0]}", return_data=True)
                    if itens_oc:
                        itens_modificados = []
                        for itm in itens_oc:
                            unid_str = itm[3] if itm[3] else "Un"
                            itens_modificados.append([itm[0], f"{itm[1]} {unid_str}", itm[2], itm[1] * (itm[2] if itm[2] else 0.0)])

                        if mostrar_preco:
                            df_itens = pd.DataFrame(itens_modificados, columns=["Peça/Serviço", "Qtd", "Preço Unit.", "Total (R$)"])
                            df_show = df_itens.copy()
                            df_show["Preço Unit."] = df_show["Preço Unit."].apply(lambda x: f"R$ {x:.2f}" if pd.notnull(x) else "R$ 0.00")
                            df_show["Total (R$)"] = df_show["Total (R$)"].apply(lambda x: f"R$ {x:.2f}" if pd.notnull(x) else "R$ 0.00")
                            st.dataframe(df_show, use_container_width=True)
                            st.write(f"### **Total da Ordem: R$ {oc[4]:.2f}**")
                        else:
                            df_itens = pd.DataFrame(itens_modificados, columns=["Peça/Serviço", "Qtd", "Preço", "Tot"])
                            st.dataframe(df_itens[["Peça/Serviço", "Qtd"]], use_container_width=True)

                    st.divider()

                    ui_botao_pdf(
                        c2,
                        f"pdf_oc_{oc[0]}",
                        lambda oid=oc[0]: gerar_pdf_oc(oid),
                        f"Doc_{oc[0]}.pdf",
                        f"btn_pdf_oc_{oc[0]}",
                        f"dl_pdf_oc_{oc[0]}",
                    )

                    with c3:
                        if oc[6]:
                            msg_oc = f"Olá {oc[1]},\nSegue resumo da nossa ordem ({tipo_txt}) Nº {oc[0]}.\n*Data:* {oc[2]}\n*Status:* {oc[3]}\n*Valor Estimado:* R$ {oc[4]:.2f}\nO PDF completo segue em anexo a esta mensagem.\nAguardamos confirmação."
                            link_oc = gerar_link_whatsapp(oc[6], msg_oc)
                            if link_oc:
                                st.link_button("📱 Enviar WhatsApp", link_oc)

                    st.divider()

                    col_act1, col_act2, col_act3, col_act4 = st.columns(4)
                    if oc[3] == 'Pendente':
                        if col_act1.button("🚫 Cancelar Pedido", key=f"canc_oc_{oc[0]}"):
                            run_query(f"UPDATE compras SET status='Cancelada' WHERE id={oc[0]}")
                            st.rerun()
                        if not ("Cotação" in tipo_txt):
                            if col_act2.button(f"📥 Concluir / Receber", key=f"rec_oc_{oc[0]}"):
                                its = run_query(f"SELECT peca_id, qtd_pedida FROM compras_itens WHERE compra_id={oc[0]}", return_data=True)
                                for pid, qtd in its: run_query(f"UPDATE estoque SET qtd = qtd + {qtd} WHERE id={pid}")
                                run_query(f"UPDATE compras SET status='Recebido' WHERE id={oc[0]}")
                                st.success("Estoque Atualizado!")
                                st.rerun()
                        else:
                            if col_act2.button(f"✅ Arquivar Cotação", key=f"arq_cot_{oc[0]}"):
                                run_query(f"UPDATE compras SET status='Concluída' WHERE id={oc[0]}")
                                st.rerun()

                        with col_act4.popover("📦 Editar Itens"):
                            st.write("**Itens Atuais na Ordem:**")
                            its_atuais = run_query(f"SELECT ci.id, e.nome, ci.qtd_pedida, ci.preco_unitario, e.unidade FROM compras_itens ci JOIN estoque e ON ci.peca_id = e.id WHERE ci.compra_id = {oc[0]}", return_data=True)

                            if its_atuais:
                                for it in its_atuais:
                                    u_s = it[4] if it[4] else "Un"
                                    with st.expander(f"✏️ {it[1]} ({it[2]} {u_s})"):
                                        with st.form(f"edit_ci_{it[0]}"):
                                            c_q, c_p = st.columns(2)
                                            nova_qtd = c_q.number_input("Nova Qtd", value=int(it[2]), min_value=1)
                                            novo_preco = c_p.number_input("Novo Preço Unit.", value=float(it[3]), min_value=0.0)

                                            c_btn1, c_btn2 = st.columns(2)
                                            if c_btn1.form_submit_button("💾 Salvar Alteração"):
                                                run_query("UPDATE compras_itens SET qtd_pedida=?, preco_unitario=? WHERE id=?", (nova_qtd, novo_preco, it[0]))
                                                novo_total = run_query(f"SELECT SUM(qtd_pedida * preco_unitario) FROM compras_itens WHERE compra_id={oc[0]}", return_data=True)[0][0] or 0.0
                                                run_query(f"UPDATE compras SET total_estimado={novo_total} WHERE id={oc[0]}")
                                                st.rerun()

                                            if c_btn2.form_submit_button("🗑️ Remover Item"):
                                                run_query(f"DELETE FROM compras_itens WHERE id={it[0]}")
                                                novo_total = run_query(f"SELECT SUM(qtd_pedida * preco_unitario) FROM compras_itens WHERE compra_id={oc[0]}", return_data=True)[0][0] or 0.0
                                                run_query(f"UPDATE compras SET total_estimado={novo_total} WHERE id={oc[0]}")
                                                st.rerun()
                            else:
                                st.info("Ordem vazia.")

                            st.divider()
                            st.write("**Adicionar Novo Item:**")
                            pecas_todas = run_query("SELECT id, nome, preco, unidade FROM estoque WHERE ativo=1", return_data=True)
                            if pecas_todas:
                                sel_p_add = st.selectbox("Peça / Serviço", pecas_todas, format_func=lambda x: f"{x[1]} ({x[3] or 'Un'})", key=f"sel_p_add_{oc[0]}")
                                c_q, c_pr = st.columns(2)
                                qtd_p_add = c_q.number_input("Quantidade", min_value=1, value=1, key=f"qtd_p_add_{oc[0]}")
                                preco_p_add = c_pr.number_input("Preço Unit.", value=float(sel_p_add[2] or 0.0), key=f"pr_p_add_{oc[0]}")

                                if st.button("➕ Adicionar à Ordem", key=f"btn_add_ci_{oc[0]}"):
                                    run_query("INSERT INTO compras_itens (compra_id, peca_id, qtd_pedida, preco_unitario) VALUES (?,?,?,?)", (oc[0], sel_p_add[0], qtd_p_add, preco_p_add))
                                    novo_total = run_query(f"SELECT SUM(qtd_pedida * preco_unitario) FROM compras_itens WHERE compra_id={oc[0]}", return_data=True)[0][0] or 0.0
                                    run_query(f"UPDATE compras SET total_estimado={novo_total} WHERE id={oc[0]}")
                                    st.rerun()

                    if oc[3] != 'Cancelada':
                        with col_act3.popover("✏️ Editar Cabeçalho"):
                            if forns_para_edicao:
                                with st.form(f"edit_oc_{oc[0]}"):
                                    curr_idx = 0
                                    for i, f_item in enumerate(forns_para_edicao):
                                        if f_item[1] == oc[1]:
                                            curr_idx = i
                                            break

                                    new_forn = st.selectbox("Fornecedor", forns_para_edicao, index=curr_idx, format_func=lambda x:x[1])

                                    try: tipo_idx = ["Padrão", "Autorização de Serviço", "Reparo", "Emergencial", "Cotação"].index(tipo_txt)
                                    except: tipo_idx = 0
                                    new_tipo = st.selectbox("Tipo", ["Padrão", "Autorização de Serviço", "Reparo", "Emergencial", "Cotação"], index=tipo_idx)

                                    if st.form_submit_button("Salvar Alterações"):
                                        txt_edit = f"Editado por {st.session_state['name']} em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                                        if oc[8]: txt_edit = oc[8] + " | " + txt_edit

                                        run_query(f"UPDATE compras SET fornecedor_id=?, tipo=?, editado_por=? WHERE id=?", (new_forn[0], new_tipo, txt_edit, oc[0]))
                                        st.success("Atualizado!")
                                        st.rerun()
                            else:
                                st.warning("Cadastre um fornecedor antes de editar.")
        else: st.info("Nenhum resultado para este filtro de data/busca.")

    with tab_nova:
        st.subheader("Emitir Pedido / Serviço")
        tipos_oc = ["Padrão", "Autorização de Serviço", "Reparo", "Emergencial", "Cotação"]
        tipo_doc = st.radio("Tipo de Ordem:", tipos_oc, horizontal=True,
                            help="Padrão/Serviço/Reparo: Exibem preços no PDF. Emergencial/Cotação: Preços ficam ocultos no PDF.")
        st.divider()
        forns = run_query("SELECT id, nome FROM fornecedores WHERE ativo=1", return_data=True)
        pecas = run_query("SELECT id, nome, preco, unidade FROM estoque WHERE ativo=1", return_data=True)

        if not forns or not pecas:
            st.warning("Cadastre Fornecedores e Peças.")
        else:
            c1, c2 = st.columns([3,1])
            f_sel = c1.selectbox("Destinatário (Fornecedor / Prestador de Serviço)", forns, format_func=lambda x: x[1])
            if c2.button("Limpar Carrinho"): st.session_state.carrinho = []
            if 'carrinho' not in st.session_state: st.session_state.carrinho = []

            with st.expander("Adicionar Item à Ordem", expanded=True):
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                p_sel = c1.selectbox("Item / Serviço", pecas, format_func=lambda x: f"{x[1]} ({x[3] or 'Un'})")

                qtd = c2.number_input("Qtd", 1, 1000, 1)
                preco_default = float(p_sel[2] if p_sel[2] else 0.0)

                mostrar_preco = tipo_doc in ["Padrão", "Autorização de Serviço", "Reparo"]
                if not mostrar_preco:
                    preco = c3.number_input("Preço Est.", value=0.0)
                else:
                    preco = c3.number_input("Preço Unit.", value=preco_default)

                if c4.button("➕ Adicionar"):
                    st.session_state.carrinho.append({"id": p_sel[0], "nome": p_sel[1], "qtd": qtd, "unidade": p_sel[3] or 'Un', "preco": preco})
                    st.rerun()

            if st.session_state.carrinho:
                df = pd.DataFrame(st.session_state.carrinho)
                df['Total'] = df['qtd'] * df['preco']
                st.dataframe(df[['nome', 'qtd', 'unidade', 'preco', 'Total']], use_container_width=True)
                if st.button(f"✅ Gerar Documento de {tipo_doc}", type="primary"):
                    dt = datetime.now().strftime("%d/%m/%Y")
                    cid = run_query("INSERT INTO compras (fornecedor_id, data_pedido, status, total_estimado, tipo, criado_por) VALUES (?,?,?,?,?,?)",
                                    (f_sel[0], dt, 'Pendente', df['Total'].sum(), tipo_doc, st.session_state['name']))
                    for i in st.session_state.carrinho:
                        run_query("INSERT INTO compras_itens (compra_id, peca_id, qtd_pedida, preco_unitario) VALUES (?,?,?,?)",
                                  (cid, i['id'], i['qtd'], i['preco']))
                    st.session_state.carrinho = []
                    st.success(f"Documento #{cid} Gerado!")
                    st.rerun()

elif menu == "Ordens de Serviço (OS)":
    st.title("🔧 Ordens de Serviço")

    if user_role in ['admin', 'supervisor']:
        tab_lista, tab_nova = st.tabs(["Gerenciar / Executar OS", "Nova OS (Agendamento)"])
    else:
        tab_lista, tab_nova = st.tabs(["Minhas O.S (Executar)", "Nova OS (Agendamento)"])

    with tab_lista:
        c_filt, c_search, c_date1, c_date2 = st.columns([1, 1, 1, 1])
        filtro_os = c_filt.radio("Status:", ["Abertas / Iniciadas", "Concluídas", "Todas"])
        termo_os = c_search.text_input("🔍 Buscar OS", placeholder="Máquina, Técnico ou Descrição...")
        dt_ini_os = c_date1.date_input("Data De:", datetime.now().date() - timedelta(days=90), key="dt1_os", format="DD/MM/YYYY")
        dt_fim_os = c_date2.date_input("Data Até:", datetime.now().date() - timedelta(days=-90), key="dt2_os", format="DD/MM/YYYY")

        q_os = """
            SELECT os.id, a.nome, u.nome_completo, os.status, os.data_abertura, os.data_fechamento,
                   os.descricao, os.ativo_id, u.id, u.telefone, os.criado_por, os.editado_por, os.data_inicio
            FROM os
            LEFT JOIN ativos a ON os.ativo_id=a.id
            LEFT JOIN usuarios u ON os.funcionario_id=u.id
            WHERE 1=1
        """

        params_os = []
        if user_role == 'operador':
            q_os += " AND (os.funcionario_id = ? OR os.criado_por = ?)"
            params_os.extend([st.session_state['user_id'], st.session_state['name']])

        if filtro_os == "Abertas / Iniciadas": q_os += " AND os.status IN ('Aberta', 'Iniciada')"
        elif filtro_os == "Concluídas": q_os += " AND os.status = 'Concluída'"

        if termo_os:
            p = sql_like(termo_os)
            q_os += " AND (IFNULL(a.nome,'') LIKE ? ESCAPE '\\' OR IFNULL(u.nome_completo,'') LIKE ? ESCAPE '\\' OR os.descricao LIKE ? ESCAPE '\\' OR CAST(os.id AS TEXT) LIKE ? ESCAPE '\\')"
            params_os.extend([p, p, p, p])
        q_os += " ORDER BY os.id DESC"

        dados_os_raw = run_query(q_os, tuple(params_os), return_data=True)
        dados_os_filtrados = []
        if dados_os_raw:
            for d in dados_os_raw:
                try:
                    data_sem_hora = d[4].split(" ")[0]
                    d_obj = datetime.strptime(data_sem_hora, "%d/%m/%Y").date()
                    if dt_ini_os <= d_obj <= dt_fim_os:
                        dados_os_filtrados.append(d)
                except:
                    dados_os_filtrados.append(d)

        if dados_os_filtrados:
            df_os_data = [[d[0], (d[1] or 'Inválido'), (d[2] or 'Não Atribuído'), d[3], d[4], d[12], d[5], d[6], d[10]] for d in dados_os_filtrados]
            df_os_excel = pd.DataFrame(df_os_data, columns=["ID", "Ativo", "Técnico", "Status", "Abertura/Agenda", "Início", "Fechamento", "Descrição", "Criado Por"])
            st.download_button("📥 Baixar Relatório (Excel)", converter_df_para_excel(df_os_excel), "relatorio_os.xlsx")
            st.divider()

            if 'page_os' not in st.session_state:
                st.session_state.page_os = 1
            items_por_pagina_os = 10
            total_os_lista = len(dados_os_filtrados)
            total_pages_os = max(1, (total_os_lista + items_por_pagina_os - 1) // items_por_pagina_os)
            if st.session_state.page_os > total_pages_os:
                st.session_state.page_os = total_pages_os
            elif st.session_state.page_os < 1:
                st.session_state.page_os = 1
            offset_os = (st.session_state.page_os - 1) * items_por_pagina_os
            pagina_os = dados_os_filtrados[offset_os:offset_os + items_por_pagina_os]
            st.caption(f"Página {st.session_state.page_os} de {total_pages_os} ({total_os_lista} O.S. no filtro).")
            po1, po2, po3 = st.columns([1, 2, 1])
            if po1.button("⬅️ Anterior OS", key="pag_os_prev", disabled=st.session_state.page_os <= 1):
                st.session_state.page_os -= 1
                st.rerun()
            if po3.button("Próxima OS ➡️", key="pag_os_next", disabled=st.session_state.page_os >= total_pages_os):
                st.session_state.page_os += 1
                st.rerun()

            os_ids_pagina = [o[0] for o in pagina_os]
            fotos_por_os = {}
            if os_ids_pagina:
                ph = ",".join("?" * len(os_ids_pagina))
                fotos_rows = run_query(
                    f"SELECT os_id, imagem FROM os_fotos WHERE os_id IN ({ph})",
                    tuple(os_ids_pagina),
                    return_data=True,
                )
                for os_id_f, img_blob in fotos_rows:
                    fotos_por_os.setdefault(os_id_f, []).append(img_blob)

            all_tecs_global = listar_tecnicos_os()

            for o in pagina_os:
                ativo_nome = o[1] if o[1] else "Ativo Excluído"
                tec_nome = o[2] if o[2] else "Técnico Não Atribuído"

                if o[3] == "Aberta": ico_os = "🔴"
                elif o[3] == "Iniciada": ico_os = "🟢"
                elif o[3] == "Concluída": ico_os = "✅"
                else: ico_os = "🚫"

                with st.expander(f"{ico_os} OS #{o[0]} - {ativo_nome} - {tec_nome} ({o[3]})", expanded=False):

                    c_img_atv, c_info_os = st.columns([1, 4])

                    ativo_img = get_ativo_imagem(o[7])
                    if ativo_img:
                        try: c_img_atv.image(Image.open(io.BytesIO(ativo_img)), use_container_width=True)
                        except: c_img_atv.info("Sem foto")
                    else:
                        c_img_atv.info("Sem foto")

                    with c_info_os:
                        st.caption(f"👤 **Criado por:** {o[10] or 'N/A'}" + (f" | ✏️ **Histórico:** {o[11]}" if o[11] else ""))

                        st.write(f"**Data Programada:** {o[4]}")
                        if o[12]: st.write(f"**Início da Execução:** {o[12]}")
                        if o[5]: st.write(f"**Fechamento:** {o[5]}")

                        st.write(f"**Problema Relatado / Anotações:**")
                        st.info(o[6] or "Nenhuma descrição registrada.")

                    fotos_lista = fotos_por_os.get(o[0], [])
                    if fotos_lista:
                        st.write("📷 **Galeria do Problema:**")
                        cols_fotos = st.columns(4)
                        for idx, img_blob in enumerate(fotos_lista):
                            try:
                                image = Image.open(io.BytesIO(img_blob))
                                cols_fotos[idx % 4].image(image, use_container_width=True)
                            except: pass

                    st.divider()

                    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)

                    with col_btn1:
                        if o[3] == 'Aberta':
                            if st.button("▶️ INICIAR O.S.", key=f"ini_{o[0]}", type="primary"):
                                agora = datetime.now().strftime("%d/%m/%Y %H:%M")
                                run_query("UPDATE os SET status='Iniciada', data_inicio=? WHERE id=?", (agora, o[0]))
                                invalidate_read_caches()
                                st.rerun()

                    ui_botao_pdf(
                        col_btn2,
                        f"pdf_os_{o[0]}",
                        lambda oid=o[0]: gerar_pdf_os(oid),
                        f"OS_{o[0]}.pdf",
                        f"btn_pdf_os_{o[0]}",
                        f"dl_pdf_os_{o[0]}",
                    )

                    with col_btn3:
                        if o[9]:
                            msg_os = f"Olá *{tec_nome}*,\nVocê tem uma OS Nº *{o[0]}*.\n*Máquina:* {ativo_nome}\n*Problema:* {o[6]}\nPor favor, acesse o sistema."
                            link_os = gerar_link_whatsapp(o[9], msg_os)
                            if link_os:
                                st.link_button("📱 Notificar Técnico", link_os)

                    with col_btn4:
                        if user_role in ['admin', 'supervisor'] and o[3] not in ['Cancelada', 'Concluída']:
                            if st.button("🚫 Cancelar OS", key=f"canc_os_{o[0]}"):
                                pecas_usadas = run_query(
                                    "SELECT peca_id, qtd_usada FROM os_pecas WHERE os_id=?",
                                    (o[0],),
                                    return_data=True,
                                )
                                for pid, qtd in pecas_usadas:
                                    run_query("UPDATE estoque SET qtd = qtd + ? WHERE id=?", (qtd, pid))
                                run_query("UPDATE os SET status='Cancelada' WHERE id=?", (o[0],))
                                st.success("OS Cancelada e peças devolvidas!")
                                st.rerun()

                    if o[3] not in ['Cancelada', 'Concluída']:
                        with st.popover("✏️ Adicionar Anotação / Reatribuir / Reagendar"):
                            st.write("**Atualizar dados do chamado**")

                            with st.form(f"edit_os_f_{o[0]}"):
                                add_desc = st.text_area("Adicionar nova observação (Não apagará o que já foi escrito)")

                                if all_tecs_global:
                                    all_tecs = all_tecs_global
                                    tecs_names = [t[1] for t in all_tecs]
                                    tec_index = tecs_names.index(tec_nome) if tec_nome in tecs_names else 0
                                    new_tec = st.selectbox("Técnico Responsável", all_tecs, index=tec_index, format_func=lambda x:x[1])

                                    new_dt_str = o[4]
                                    if user_role in ['admin', 'supervisor']:
                                        st.divider()
                                        st.write("🗓️ **Reagendar Serviço**")
                                        try:
                                            curr_date_obj = datetime.strptime(o[4], "%d/%m/%Y %H:%M")
                                            curr_d = curr_date_obj.date()
                                            curr_t = curr_date_obj.time()
                                        except:
                                            curr_d = datetime.now().date()
                                            curr_t = datetime.now().time()

                                        c_d, c_t = st.columns(2)
                                        new_d = c_d.date_input("Nova Data", curr_d)
                                        new_t = c_t.time_input("Nova Hora", curr_t)

                                    if st.form_submit_button("Salvar Atualização"):
                                        if user_role in ['admin', 'supervisor']:
                                            new_dt_str = f"{new_d.strftime('%d/%m/%Y')} {new_t.strftime('%H:%M')}"

                                        txt_edit_os = f"Atualizado por {st.session_state['name']} em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                                        if o[11]: txt_edit_os = o[11] + " | " + txt_edit_os

                                        final_desc = o[6] or ""
                                        if add_desc.strip():
                                            stamp = f"\n\n--- 🗣️ {st.session_state['name']} comentou em {datetime.now().strftime('%d/%m/%Y %H:%M')} ---\n"
                                            final_desc += stamp + add_desc.strip()

                                        run_query("UPDATE os SET descricao=?, funcionario_id=?, editado_por=?, data_abertura=? WHERE id=?",
                                                  (final_desc, new_tec[0], txt_edit_os, new_dt_str, o[0]))
                                        invalidate_read_caches()
                                        st.success("OS atualizada com sucesso!")
                                        st.rerun()
                                else:
                                    st.warning("Cadastre um técnico para editar.")

                    if o[3] == 'Iniciada':
                        st.divider()

                        chk_db = run_query(
                            "SELECT id, passo, concluido FROM os_checklist WHERE os_id=?",
                            (o[0],),
                            return_data=True,
                        )
                        all_done = all(bool(c[2]) for c in chk_db) if chk_db else True

                        if chk_db:
                            st.write("📋 **CHECKLIST DE EXECUÇÃO (OBRIGATÓRIO)**")
                            with st.form(f"chk_form_{o[0]}"):
                                chk_vals = {}
                                for chk in chk_db:
                                    chk_vals[chk[0]] = st.checkbox(chk[1], value=bool(chk[2]))
                                if st.form_submit_button("💾 Salvar Checklist"):
                                    for chk in chk_db:
                                        novo = 1 if chk_vals[chk[0]] else 0
                                        if novo != chk[2]:
                                            run_query(
                                                "UPDATE os_checklist SET concluido=? WHERE id=?",
                                                (novo, chk[0]),
                                            )
                                    st.rerun()
                            chk_atual = run_query(
                                "SELECT concluido FROM os_checklist WHERE os_id=?",
                                (o[0],),
                                return_data=True,
                            )
                            all_done = all(bool(r[0]) for r in chk_atual) if chk_atual else True
                            if not all_done:
                                st.warning("⚠️ Você precisa concluir todos os passos do Checklist acima para poder finalizar esta OS.")
                            st.divider()

                        st.write("🛠️ **Consumo de Peças**")

                        if o[7]:
                            pv = run_query(
                                "SELECT e.id, e.nome, e.qtd, e.unidade FROM ativos_pecas ap JOIN estoque e ON ap.peca_id=e.id WHERE ap.ativo_id=?",
                                (o[7],),
                                return_data=True,
                            )
                            if pv:
                                st.info("Peças sugeridas para esta máquina (BOM):")
                                for p in pv:
                                    with st.form(f"add_sug_{o[0]}_{p[0]}", clear_on_submit=True):
                                        c1, c2, c3 = st.columns([3, 1, 1])
                                        unid_str = p[3] if p[3] else "Un"
                                        c1.write(f"{p[1]} (Estoque: {p[2]} {unid_str})")
                                        q_v = c2.number_input("Qtd", 1, p[2] if p[2]>0 else 1, 1)
                                        if c3.form_submit_button("Aplicar"):
                                            if p[2]>=q_v:
                                                run_query("INSERT INTO os_pecas (os_id, peca_id, qtd_usada) VALUES (?,?,?)", (o[0], p[0], q_v))
                                                run_query("UPDATE estoque SET qtd=qtd-? WHERE id=?", (q_v, p[0]))
                                                st.rerun()
                                            else: st.error("Sem estoque suficiente")

                        with st.expander("Pesquisar outra peça no estoque geral"):
                            p_all = run_query("SELECT id, nome, qtd, unidade FROM estoque WHERE qtd>0 AND ativo=1", return_data=True)
                            if p_all:
                                c1_a, c2_a, c3_a = st.columns([3, 1, 1])
                                pa = c1_a.selectbox("Selecione a Peça", p_all, format_func=lambda x:f"{x[1]} ({x[3] or 'Un'})", key=f"ps_{o[0]}")

                                qa = c2_a.number_input("Qtd", 1, pa[2], 1, key=f"qs_{o[0]}")
                                if c3_a.button("Aplicar Peça", key=f"bs_{o[0]}"):
                                    run_query("INSERT INTO os_pecas (os_id, peca_id, qtd_usada) VALUES (?,?,?)", (o[0], pa[0], qa))
                                    run_query(f"UPDATE estoque SET qtd=qtd-{qa} WHERE id={pa[0]}")
                                    st.rerun()

                        usadas = run_query(f"SELECT op.id, e.id, e.nome, op.qtd_usada, e.unidade FROM os_pecas op JOIN estoque e ON op.peca_id=e.id WHERE op.os_id={o[0]}", return_data=True)
                        if usadas:
                            texto_usadas = ", ".join([f"{u[2]} ({u[3]} {u[4] or 'Un'})" for u in usadas])
                            st.write("**Itens já aplicados nesta O.S.:** " + texto_usadas)

                            if user_role in ['admin', 'supervisor']:
                                with st.popover("📦 Editar / Devolver Peças"):
                                    st.write("Ajuste as quantidades já lançadas:")
                                    for u in usadas:
                                        u_s = u[4] if u[4] else "Un"
                                        with st.expander(f"✏️ {u[2]} (Atual: {u[3]} {u_s})"):
                                            with st.form(f"edit_op_{u[0]}"):
                                                nova_qtd = st.number_input("Nova Quantidade Correta", value=int(u[3]), min_value=1)
                                                c_b1, c_b2 = st.columns(2)

                                                if c_b1.form_submit_button("💾 Salvar Correção"):
                                                    diff = nova_qtd - u[3]
                                                    if diff > 0: # Pegando mais do estoque
                                                        est_atual = run_query(f"SELECT qtd FROM estoque WHERE id={u[1]}", return_data=True)[0][0]
                                                        if est_atual < diff:
                                                            st.error("Estoque insuficiente para esse aumento!")
                                                        else:
                                                            run_query("UPDATE os_pecas SET qtd_usada=? WHERE id=?", (nova_qtd, u[0]))
                                                            run_query(f"UPDATE estoque SET qtd=qtd-{diff} WHERE id={u[1]}")
                                                            st.rerun()
                                                    elif diff < 0: # Devolvendo para o estoque
                                                        run_query("UPDATE os_pecas SET qtd_usada=? WHERE id=?", (nova_qtd, u[0]))
                                                        run_query(f"UPDATE estoque SET qtd=qtd+{abs(diff)} WHERE id={u[1]}")
                                                        st.rerun()
                                                    else:
                                                        st.rerun()

                                                if c_b2.form_submit_button("🗑️ Devolver Tudo"):
                                                    run_query(f"UPDATE estoque SET qtd=qtd+{u[3]} WHERE id={u[1]}")
                                                    run_query(f"DELETE FROM os_pecas WHERE id={u[0]}")
                                                    st.rerun()

                        st.divider()

                        st.write("O serviço foi finalizado?")

                        pode_concluir = True
                        if chk_db and not all_done:
                            pode_concluir = False

                        if pode_concluir:
                            if st.button(f"✅ CONCLUIR ORDEM DE SERVIÇO", type="primary", key=f"c_{o[0]}"):
                                fim = datetime.now().strftime("%d/%m/%Y %H:%M")
                                inicio_atual = o[12] if o[12] else fim
                                run_query(
                                    "UPDATE os SET status='Concluída', data_fechamento=?, data_inicio=? WHERE id=?",
                                    (fim, inicio_atual, o[0]),
                                )
                                invalidate_read_caches()
                                st.rerun()
                        else:
                            st.button(f"🔒 CONCLUIR ORDEM DE SERVIÇO", disabled=True, key=f"block_c_{o[0]}")

                    if o[3] == 'Concluída':
                        if user_role in ['admin', 'supervisor']:
                            st.divider()
                            st.warning("⚠️ Esta O.S. já foi concluída. Caso precise alterar informações ou lançar peças esquecidas, você pode reabri-la.")
                            if st.button("🔄 REABRIR ORDEM DE SERVIÇO", key=f"reabrir_{o[0]}"):
                                agora_reab = datetime.now().strftime("%d/%m/%Y %H:%M")
                                txt_reab = f"Reaberta por {st.session_state['name']} em {agora_reab} (reinício do tempo de reparo)"
                                if o[11]:
                                    txt_reab = o[11] + " | " + txt_reab
                                run_query(
                                    "UPDATE os SET status='Iniciada', data_fechamento='', data_inicio=?, editado_por=? WHERE id=?",
                                    (agora_reab, txt_reab, o[0]),
                                )
                                invalidate_read_caches()
                                st.rerun()

        else: st.info("Nenhuma OS encontrada para este filtro de data/busca.")

    with tab_nova:
        st.subheader("Emitir Nova Ordem de Serviço (Agendamento)")
        at = run_query("SELECT id, nome, imagem FROM ativos WHERE ativo=1", return_data=True)
        fu = run_query("SELECT id, nome_completo FROM usuarios WHERE role IN ('operador', 'supervisor', 'admin')", return_data=True)
        pops_db = run_query("SELECT id, nome FROM pop_modelos", return_data=True)

        if at and fu:
            c1, c2 = st.columns(2)
            a = c1.selectbox("Ativo / Máquina", at, format_func=lambda x: x[1])

            if a and a[2]:
                try: c1.image(Image.open(io.BytesIO(a[2])), width=180)
                except: pass

            f = c2.selectbox("Atribuir ao Técnico", fu, format_func=lambda x: x[1])

            st.divider()

            pop_sel = None
            if pops_db:
                st.write("📋 **Procedimento Operacional Padrão (Opcional)**")
                lista_pops = [(0, "Nenhum POP")] + pops_db
                pop_sel = st.selectbox("Vincular Checklist à OS:", lista_pops, format_func=lambda x: x[1])
                st.divider()

            st.write("🗓️ **Programação do Serviço**")
            c_data, c_hora = st.columns(2)
            data_agendada = c_data.date_input("Data do Serviço", datetime.now().date(), format="DD/MM/YYYY")
            hora_agendada = c_hora.time_input("Hora do Serviço", datetime.now().time())

            d = st.text_area("Descrição do Problema / Atividade", key="os_desc_input")
            st.write("📷 Fotos do Problema (Opcional)")
            fotos_upload = st.file_uploader("Enviar Fotos", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'], key="os_fotos_input")

            if st.button("Gerar O.S. / Agendar", type="primary"):
                dt_str = f"{data_agendada.strftime('%d/%m/%Y')} {hora_agendada.strftime('%H:%M')}"
                os_id = run_query("INSERT INTO os (ativo_id, funcionario_id, descricao, data_abertura, status, criado_por) VALUES (?,?,?,?,?,?)",
                                  (a[0], f[0], d, dt_str, 'Aberta', st.session_state['name']))

                if pop_sel and pop_sel[0] > 0:
                    passos = run_query(f"SELECT passo FROM pop_passos WHERE pop_id={pop_sel[0]}", return_data=True)
                    for passo in passos:
                        run_query("INSERT INTO os_checklist (os_id, passo, concluido) VALUES (?, ?, 0)", (os_id, passo[0]))

                if fotos_upload:
                    for foto in fotos_upload:
                        blob = otimizar_imagem(foto)
                        if blob:
                            run_query(
                                "INSERT INTO os_fotos (os_id, imagem) VALUES (?, ?)",
                                (os_id, blob),
                            )

                st.session_state.pop("os_desc_input", None)
                invalidate_read_caches()
                st.success("Ordem de Serviço gerada com sucesso!")
                st.rerun()
        else: st.warning("Certifique-se de ter Máquinas e Usuários cadastrados.")

elif menu == "Gestão Usuários":
    st.title("👥 Controle de Acesso e Técnicos")
    tab_lista, tab_nova = st.tabs(["Gerenciar Existentes", "Cadastrar Usuário / Técnico"])

    with tab_lista:
        us = run_query("SELECT id, username, nome_completo, role, telefone, password FROM usuarios", return_data=True)
        for u in us:
            c1, c2, c3 = st.columns([4, 1, 1])
            tel_str = f" | 📱 {u[4]}" if u[4] else ""
            c1.write(f"**{u[2]}** (Login: {u[1]}) - Permissão: {u[3].upper()}{tel_str}")

            with c2.popover("✏️ Editar"):
                with st.form(f"edit_u_{u[0]}"):
                    n_nome = st.text_input("Nome", u[2])
                    n_user = st.text_input("Login", u[1])
                    n_pass = st.text_input("Nova senha (deixe em branco para manter)", type="password", placeholder="••••••")
                    n_tel = st.text_input("Telefone (Apenas números)", u[4] or "")

                    roles = ["operador", "supervisor", "admin"]
                    idx_role = roles.index(u[3]) if u[3] in roles else 0
                    n_role = st.selectbox("Nível de Acesso", roles, index=idx_role)

                    if st.form_submit_button("Salvar Alterações"):
                        if u[1] == 'admin' and n_role != 'admin':
                            st.error("O Administrador principal não pode ter o nível reduzido.")
                        else:
                            if n_pass.strip():
                                pwd_save = hash_password(n_pass)
                                run_query("UPDATE usuarios SET nome_completo=?, username=?, password=?, telefone=?, role=? WHERE id=?",
                                          (n_nome, n_user, pwd_save, n_tel, n_role, u[0]))
                            else:
                                run_query("UPDATE usuarios SET nome_completo=?, username=?, telefone=?, role=? WHERE id=?",
                                          (n_nome, n_user, n_tel, n_role, u[0]))
                            invalidate_read_caches()
                            st.success("Usuário atualizado!")
                            st.rerun()

            if u[1] != 'admin' and c3.button("🗑️ Remover", key=f"d_u_{u[0]}"):
                run_query(f"DELETE FROM usuarios WHERE id={u[0]}")
                st.rerun()
            st.divider()

    with tab_nova:
        with st.form("new_user", clear_on_submit=True):
            nu = st.text_input("Login de Acesso")
            np = st.text_input("Senha", type="password")
            nn = st.text_input("Nome Completo")
            nt = st.text_input("Telefone / WhatsApp (Apenas números)")
            nr = st.selectbox("Nível de Acesso", ["operador", "supervisor", "admin"], help="Operadores executam O.S. Supervisores e Admins gerenciam o sistema.")
            if st.form_submit_button("Criar Usuário"):
                try:
                    run_query("INSERT INTO usuarios (username, password, role, nome_completo, telefone) VALUES (?,?,?,?,?)", (nu, hash_password(np), nr, nn, nt))
                    invalidate_read_caches()
                    st.success("Usuário criado com sucesso!")
                    st.rerun()
                except: st.error("Este Login já existe. Tente outro.")
