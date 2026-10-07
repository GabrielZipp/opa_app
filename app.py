import os
import re
import sqlite3
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

try:
    from streamlit_calendar import calendar
    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False

# =========================================================
# CONFIG
# =========================================================
DB_PATH = os.getenv("OPA_DB", "opa.db")
SENHA_COORD = os.getenv("OPA_SENHA", "opa2025")

st.set_page_config(page_title="O.P.A · Coordenação", page_icon="🙌", layout="wide")

TIPOS_EVENTO = {
    "grupo":    {"label": "🙌 Grupo semanal",    "cor": "#7c5cff"},
    "retiro":   {"label": "🏕️ Retiro",           "cor": "#f59e0b"},
    "especial": {"label": "🎉 Evento especial",  "cor": "#ec4899"},
    "outro":    {"label": "📌 Outro",            "cor": "#6b7280"},
}
OPCOES_TIPO_FILTRO = ["Todos", "Grupos", "Retiros", "Especiais", "Outros"]


# =========================================================
# HELPERS
# =========================================================
def normalizar_nome(nome):
    """Remove acentos, colapsa espaços, deixa minúsculo — pra comparação."""
    if not nome:
        return ""
    n = " ".join(str(nome).strip().split()).lower()
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode("ascii")
    return n


def formatar_telefone(valor):
    if valor is None:
        return ""
    numeros = re.sub(r"\D", "", str(valor))
    if not numeros:
        return ""
    if len(numeros) <= 2:
        return f"({numeros}"
    if len(numeros) <= 6:
        return f"({numeros[:2]}) {numeros[2:]}"
    if len(numeros) <= 10:
        return f"({numeros[:2]}) {numeros[2:6]}-{numeros[6:]}"
    return f"({numeros[:2]}) {numeros[2:7]}-{numeros[7:11]}"


def limpar_telefone(valor):
    if not valor:
        return ""
    return re.sub(r"\D", "", str(valor))


def link_whatsapp(telefone, nome="", template=None):
    num = limpar_telefone(telefone)
    if not num:
        return None
    if not num.startswith("55"):
        num = "55" + num
    if template is None:
        primeiro = nome.split()[0] if nome else ""
        template = f"Oi{(' ' + primeiro) if primeiro else ''}! Tudo bem? Sentimos sua falta no grupo 💛"
    return f"https://wa.me/{num}?text={urllib.parse.quote(template)}"


def fmt_data(iso):
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return iso


def nome_dia_semana(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"][d.weekday()]
    except Exception:
        return ""


def saudacao_por_hora():
    h = datetime.now().hour
    if h < 12:
        return "Bom dia"
    if h < 18:
        return "Boa tarde"
    return "Boa noite"


def fmt_data_extenso(d: date):
    meses = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    dias = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
            "Sexta-feira", "Sábado", "Domingo"]
    return f"{dias[d.weekday()]}, {d.day} de {meses[d.month - 1]} de {d.year}"


def fmt_dias(n):
    if n is None:
        return "—"
    if n == 0:
        return "hoje"
    if n == 1:
        return "ontem"
    return f"{n} dias"


def _on_change_tel(key):
    raw = st.session_state.get(key, "")
    st.session_state[key] = formatar_telefone(raw)


def calcular_idade(data_nasc_iso):
    if not data_nasc_iso:
        return None
    try:
        d = datetime.strptime(data_nasc_iso, "%Y-%m-%d").date()
        hoje = date.today()
        return hoje.year - d.year - ((hoje.month, hoje.day) < (d.month, d.day))
    except Exception:
        return None


def tipo_evento_info(tipo):
    return TIPOS_EVENTO.get(tipo or "grupo", TIPOS_EVENTO["outro"])


def cor_evento(ev, hoje_iso):
    if ev["data"] < hoje_iso:
        return "#4b5563"
    return tipo_evento_info(ev.get("tipo")).get("cor", "#7c5cff")


# =========================================================
# BANCO
# =========================================================
def conn_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with conn_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contatos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                data_nascimento TEXT,
                telefone TEXT,
                criado_em TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS presencas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contato_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                presente INTEGER DEFAULT 0,
                UNIQUE(contato_id, data),
                FOREIGN KEY(contato_id) REFERENCES contatos(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL UNIQUE,
                titulo TEXT,
                observacoes TEXT,
                criado_em TEXT
            )
        """)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(contatos)").fetchall()}
        if "nome_responsavel" not in cols:
            conn.execute("ALTER TABLE contatos ADD COLUMN nome_responsavel TEXT")
        if "telefone_responsavel" not in cols:
            conn.execute("ALTER TABLE contatos ADD COLUMN telefone_responsavel TEXT")
        if "observacoes" not in cols:
            conn.execute("ALTER TABLE contatos ADD COLUMN observacoes TEXT")
        cols_ev = {row[1] for row in conn.execute("PRAGMA table_info(eventos)").fetchall()}
        if "tipo" not in cols_ev:
            conn.execute("ALTER TABLE eventos ADD COLUMN tipo TEXT DEFAULT 'grupo'")
        conn.commit()


# --------------------- Contatos -------------------------
def nome_ja_existe(nome, ignorar_id=None):
    """Retorna o nome já existente (string) ou None. Ignora acentos/espaços/caixa."""
    alvo = normalizar_nome(nome)
    if not alvo:
        return None
    with conn_db() as conn:
        rows = conn.execute("SELECT id, nome FROM contatos").fetchall()
    for cid, nome_db in rows:
        if ignorar_id is not None and int(cid) == int(ignorar_id):
            continue
        if normalizar_nome(nome_db) == alvo:
            return nome_db
    return None


def criar_contato(nome, nascimento, telefone, nome_responsavel,
                  telefone_responsavel, observacoes=""):
    nome = nome.strip()
    nome_responsavel = (nome_responsavel or "").strip()
    if not nome:
        return False, "O nome do jovem é obrigatório."
    if not nome_responsavel:
        return False, "O nome do responsável é obrigatório."
    if not limpar_telefone(telefone_responsavel):
        return False, "O telefone do responsável é obrigatório."

    # ---- Nome duplicado ----
    existente = nome_ja_existe(nome)
    if existente:
        return False, f"⚠️ Já existe um jovem cadastrado com esse nome: **{existente}**."

    with conn_db() as conn:
        c = conn.cursor()
        tel_limpo = limpar_telefone(telefone)
        if tel_limpo:
            c.execute("""SELECT nome FROM contatos
                WHERE REPLACE(REPLACE(REPLACE(REPLACE(telefone,'(',''),')',''),'-',''),' ','') = ?""",
                      (tel_limpo,))
            ja_tel = c.fetchone()
            if ja_tel:
                return False, f"Já existe um contato com esse telefone ({ja_tel[0]})."
        c.execute(
            """INSERT INTO contatos (nome, data_nascimento, telefone,
               nome_responsavel, telefone_responsavel, observacoes, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (nome, nascimento, telefone.strip(), nome_responsavel,
             telefone_responsavel.strip(), (observacoes or "").strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    return True, f"✅ {nome} cadastrado com sucesso!"


def listar_contatos(busca=""):
    with conn_db() as conn:
        base = ("SELECT id, nome, data_nascimento, telefone, nome_responsavel, "
                "telefone_responsavel, observacoes, criado_em FROM contatos ")
        if busca:
            busca_txt = busca.strip()
            busca_num = re.sub(r"\D", "", busca_txt)
            return pd.read_sql_query(
                base + """WHERE nome LIKE ?
                       OR nome_responsavel LIKE ?
                       OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(telefone,''),'(',''),')',''),'-',''),' ','') LIKE ?
                       OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(telefone_responsavel,''),'(',''),')',''),'-',''),' ','') LIKE ?
                       ORDER BY nome COLLATE NOCASE""",
                conn, params=(f"%{busca_txt}%", f"%{busca_txt}%",
                              f"%{busca_num}%", f"%{busca_num}%"),
            )
        return pd.read_sql_query(base + "ORDER BY nome COLLATE NOCASE", conn)


def obter_contato(contato_id):
    with conn_db() as conn:
        df = pd.read_sql_query("SELECT * FROM contatos WHERE id = ?", conn, params=(contato_id,))
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def atualizar_contato(contato_id, nome, nascimento, telefone, nome_resp, tel_resp, observacoes=""):
    # ---- Nome duplicado (ignorando a si mesmo) ----
    existente = nome_ja_existe(nome, ignorar_id=contato_id)
    if existente:
        return False, f"⚠️ Já existe outro jovem com esse nome: **{existente}**."

    with conn_db() as conn:
        conn.execute(
            """UPDATE contatos SET nome = ?, data_nascimento = ?, telefone = ?,
               nome_responsavel = ?, telefone_responsavel = ?, observacoes = ? WHERE id = ?""",
            (nome.strip(), nascimento, telefone.strip(),
             nome_resp.strip(), tel_resp.strip(), (observacoes or "").strip(), contato_id),
        )
        conn.commit()
    return True, "Atualizado."


def deletar_contato(contato_id):
    with conn_db() as conn:
        conn.execute("DELETE FROM presencas WHERE contato_id = ?", (contato_id,))
        conn.execute("DELETE FROM contatos WHERE id = ?", (contato_id,))
        conn.commit()


def contar_contatos():
    with conn_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM contatos").fetchone()[0]


def ultimos_contatos(limite=5):
    with conn_db() as conn:
        return pd.read_sql_query(
            "SELECT nome, telefone, criado_em FROM contatos ORDER BY id DESC LIMIT ?",
            conn, params=(limite,),
        )


def novos_recentes(dias=30, limite=5):
    corte = (date.today() - timedelta(days=dias)).isoformat()
    with conn_db() as conn:
        return pd.read_sql_query(
            """SELECT nome, telefone, criado_em FROM contatos
               WHERE substr(criado_em, 1, 10) >= ?
               ORDER BY criado_em DESC LIMIT ?""",
            conn, params=(corte, limite),
        )


def resumo_alertas():
    hoje = date.today()
    ha_30 = (hoje - timedelta(days=30)).isoformat()
    with conn_db() as conn:
        sumidos = conn.execute(
            """SELECT COUNT(*) FROM contatos c
               WHERE EXISTS (SELECT 1 FROM presencas WHERE contato_id = c.id AND presente = 1)
               AND NOT EXISTS (SELECT 1 FROM presencas
                              WHERE contato_id = c.id AND presente = 1 AND data >= ?)""",
            (ha_30,),
        ).fetchone()[0]
        nunca = conn.execute(
            """SELECT COUNT(*) FROM contatos c
               WHERE NOT EXISTS (SELECT 1 FROM presencas
                                WHERE contato_id = c.id AND presente = 1)"""
        ).fetchone()[0]
        novos = conn.execute(
            "SELECT COUNT(*) FROM contatos WHERE substr(criado_em, 1, 10) >= ?",
            (ha_30,),
        ).fetchone()[0]
    return sumidos, nunca, novos


def mapa_assiduidade():
    with conn_db() as conn:
        df = pd.read_sql_query(
            "SELECT contato_id, COUNT(*) AS c FROM presencas WHERE presente = 1 GROUP BY contato_id",
            conn,
        )
    return dict(zip(df["contato_id"], df["c"]))


# --------------------- Presenças ------------------------
def presencas_do_dia(data_iso):
    with conn_db() as conn:
        df = pd.read_sql_query(
            "SELECT contato_id, presente FROM presencas WHERE data = ?",
            conn, params=(data_iso,),
        )
    return dict(zip(df["contato_id"], df["presente"]))


def salvar_presenca(contato_id, data_iso, presente):
    with conn_db() as conn:
        conn.execute(
            """INSERT INTO presencas (contato_id, data, presente) VALUES (?, ?, ?)
               ON CONFLICT(contato_id, data) DO UPDATE SET presente = excluded.presente""",
            (contato_id, data_iso, 1 if presente else 0),
        )
        if presente:
            conn.execute(
                "INSERT OR IGNORE INTO eventos (data, titulo, criado_em, tipo) VALUES (?, '', ?, 'grupo')",
                (data_iso, datetime.now().isoformat(timespec="seconds")),
            )
        conn.commit()


def marcar_todos_presentes(data_iso, contato_ids, presente=True):
    if not contato_ids:
        return
    with conn_db() as conn:
        for cid in contato_ids:
            conn.execute(
                """INSERT INTO presencas (contato_id, data, presente) VALUES (?, ?, ?)
                   ON CONFLICT(contato_id, data) DO UPDATE SET presente = excluded.presente""",
                (cid, data_iso, 1 if presente else 0),
            )
        if presente:
            conn.execute(
                "INSERT OR IGNORE INTO eventos (data, titulo, criado_em, tipo) VALUES (?, '', ?, 'grupo')",
                (data_iso, datetime.now().isoformat(timespec="seconds")),
            )
        conn.commit()


def ultimo_evento_com_presenca():
    with conn_db() as conn:
        row = conn.execute(
            "SELECT data FROM presencas WHERE presente = 1 ORDER BY data DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None, 0, 0
        data_iso = row[0]
        total = conn.execute("SELECT COUNT(*) FROM contatos").fetchone()[0]
        presentes = conn.execute(
            "SELECT COUNT(*) FROM presencas WHERE data = ? AND presente = 1", (data_iso,)
        ).fetchone()[0]
    return data_iso, presentes, total


def dados_ultimos_grupos(n=4, tipo=None):
    with conn_db() as conn:
        if tipo:
            df = pd.read_sql_query(
                """SELECT p.data, COUNT(*) AS presentes
                   FROM presencas p
                   JOIN eventos e ON e.data = p.data
                   WHERE p.presente = 1 AND e.tipo = ?
                   GROUP BY p.data ORDER BY p.data DESC LIMIT ?""",
                conn, params=(tipo, n),
            )
        else:
            df = pd.read_sql_query(
                """SELECT data, COUNT(*) AS presentes
                   FROM presencas WHERE presente = 1
                   GROUP BY data ORDER BY data DESC LIMIT ?""",
                conn, params=(n,),
            )
    if df.empty:
        return df
    total = contar_contatos()
    df["total_cadastrado"] = total
    df["percentual"] = df["presentes"].apply(
        lambda x: round(x / total * 100, 1) if total > 0 else 0
    )
    return df.sort_values("data")


# --------------------- Eventos / Agenda -----------------
def criar_evento(data_iso, titulo="", observacoes="", tipo="grupo"):
    with conn_db() as conn:
        try:
            conn.execute(
                """INSERT INTO eventos (data, titulo, observacoes, criado_em, tipo)
                   VALUES (?, ?, ?, ?, ?)""",
                (data_iso, titulo.strip(), observacoes.strip(),
                 datetime.now().isoformat(timespec="seconds"), tipo),
            )
            conn.commit()
            return True, f"✅ Evento em {fmt_data(data_iso)} agendado!"
        except sqlite3.IntegrityError:
            return False, "Já existe um evento nessa data. Escolha outra data."


def atualizar_evento(evento_id, titulo, observacoes, tipo=None):
    with conn_db() as conn:
        if tipo is not None:
            conn.execute(
                "UPDATE eventos SET titulo = ?, observacoes = ?, tipo = ? WHERE id = ?",
                (titulo.strip(), observacoes.strip(), tipo, evento_id),
            )
        else:
            conn.execute(
                "UPDATE eventos SET titulo = ?, observacoes = ? WHERE id = ?",
                (titulo.strip(), observacoes.strip(), evento_id),
            )
        conn.commit()


def deletar_evento(evento_id):
    with conn_db() as conn:
        conn.execute("DELETE FROM eventos WHERE id = ?", (evento_id,))
        conn.commit()


def listar_eventos(filtro="todos", tipo=None):
    hoje = date.today().isoformat()
    with conn_db() as conn:
        params = []
        conds = []
        if filtro == "futuros":
            conds.append("data >= ?")
            params.append(hoje)
            order = "ORDER BY data ASC"
        elif filtro == "passados":
            conds.append("data < ?")
            params.append(hoje)
            order = "ORDER BY data DESC"
        else:
            order = "ORDER BY data DESC"

        if tipo:
            conds.append("tipo = ?")
            params.append(tipo)

        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        return pd.read_sql_query(
            f"SELECT * FROM eventos {where} {order}", conn, params=tuple(params)
        )


def contar_eventos_do_mes():
    hoje = date.today()
    ini = hoje.replace(day=1).isoformat()
    if hoje.month == 12:
        fim = hoje.replace(year=hoje.year + 1, month=1, day=1).isoformat()
    else:
        fim = hoje.replace(month=hoje.month + 1, day=1).isoformat()
    with conn_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM eventos WHERE data >= ? AND data < ?", (ini, fim)
        ).fetchone()[0]


def estatisticas_evento(data_iso):
    with conn_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM contatos").fetchone()[0]
        presentes = conn.execute(
            "SELECT COUNT(*) FROM presencas WHERE data = ? AND presente = 1", (data_iso,)
        ).fetchone()[0]
    return presentes, total


def ausentes_do_dia(data_iso):
    with conn_db() as conn:
        return pd.read_sql_query(
            """SELECT c.nome, c.telefone FROM contatos c
               WHERE c.id NOT IN (SELECT contato_id FROM presencas WHERE data = ? AND presente = 1)
               ORDER BY c.nome COLLATE NOCASE""",
            conn, params=(data_iso,),
        )


# --------------------- Frequência -----------------------
def frequencia_por_ano(ano):
    ini, fim = f"{ano}-01-01", f"{ano}-12-31"
    with conn_db() as conn:
        total_grupos = conn.execute(
            "SELECT COUNT(DISTINCT data) FROM presencas WHERE presente = 1 AND data >= ? AND data <= ?",
            (ini, fim),
        ).fetchone()[0]
        df = pd.read_sql_query(
            """SELECT c.id, c.nome, c.telefone,
                      COALESCE(SUM(CASE WHEN p.presente = 1 THEN 1 ELSE 0 END), 0) AS grupos_presentes,
                      MAX(CASE WHEN p.presente = 1 THEN p.data END) AS ultima_vez
               FROM contatos c
               LEFT JOIN presencas p ON p.contato_id = c.id AND p.data >= ? AND p.data <= ?
               GROUP BY c.id, c.nome, c.telefone
               ORDER BY grupos_presentes DESC, c.nome COLLATE NOCASE""",
            conn, params=(ini, fim),
        )
    df["total_grupos"] = total_grupos
    df["percentual"] = df["grupos_presentes"].apply(
        lambda x: round((x / total_grupos * 100), 1) if total_grupos > 0 else 0.0
    )
    return df, total_grupos


def grupos_por_mes(ano):
    with conn_db() as conn:
        df = pd.read_sql_query(
            """SELECT substr(data, 6, 2) AS mes, COUNT(DISTINCT data) AS grupos
               FROM presencas WHERE presente = 1 AND data >= ? AND data <= ?
               GROUP BY mes ORDER BY mes""",
            conn, params=(f"{ano}-01-01", f"{ano}-12-31"),
        )
    meses = [f"{m:02d}" for m in range(1, 13)]
    df = df.set_index("mes").reindex(meses, fill_value=0).reset_index()
    df.columns = ["mes", "grupos"]
    return df


def dados_heatmap(ano):
    ini, fim = f"{ano}-01-01", f"{ano}-12-31"
    with conn_db() as conn:
        df_jovens = pd.read_sql_query(
            "SELECT id, nome FROM contatos ORDER BY nome COLLATE NOCASE", conn
        )
        df_pres = pd.read_sql_query(
            "SELECT contato_id, data FROM presencas WHERE presente = 1 AND data >= ? AND data <= ?",
            conn, params=(ini, fim),
        )
        df_grupos = pd.read_sql_query(
            "SELECT DISTINCT data FROM presencas WHERE presente = 1 AND data >= ? AND data <= ?",
            conn, params=(ini, fim),
        )

    if df_jovens.empty:
        return pd.DataFrame(columns=["nome", "mes", "presencas", "total_mes", "percentual", "total_ano"])

    if df_grupos.empty:
        grupos_por_mes_d = {m: 0 for m in range(1, 13)}
    else:
        df_grupos["mes"] = df_grupos["data"].str[5:7].astype(int)
        grupos_por_mes_d = df_grupos.groupby("mes").size().to_dict()

    if df_pres.empty:
        pres_por_jm, total_por_jovem = {}, {}
    else:
        df_pres["mes"] = df_pres["data"].str[5:7].astype(int)
        pres_por_jm = df_pres.groupby(["contato_id", "mes"]).size().to_dict()
        total_por_jovem = df_pres.groupby("contato_id").size().to_dict()

    linhas = []
    for _, j in df_jovens.iterrows():
        jid = j["id"]
        total_j = total_por_jovem.get(jid, 0)
        for mes in range(1, 13):
            total_mes = grupos_por_mes_d.get(mes, 0)
            pres = pres_por_jm.get((jid, mes), 0)
            pct = (pres / total_mes * 100) if total_mes > 0 else 0
            linhas.append({
                "nome": j["nome"], "mes": mes, "presencas": pres,
                "total_mes": total_mes, "percentual": pct, "total_ano": total_j,
            })
    return pd.DataFrame(linhas)


def resolver_periodo(opcao):
    hoje = date.today()
    if opcao == "Este mês":
        return hoje.replace(day=1), hoje, "neste mês"
    if opcao == "Últimos 30 dias":
        return hoje - timedelta(days=30), hoje, "nos últimos 30 dias"
    if opcao == "Últimos 60 dias":
        return hoje - timedelta(days=60), hoje, "nos últimos 60 dias"
    if opcao == "Últimos 90 dias":
        return hoje - timedelta(days=90), hoje, "nos últimos 90 dias"
    return hoje.replace(day=1), hoje, "neste mês"


def jovens_em_alerta(data_ini_iso, data_fim_iso):
    with conn_db() as conn:
        df = pd.read_sql_query(
            """SELECT c.id, c.nome, c.telefone, c.criado_em,
                      (SELECT MAX(data) FROM presencas WHERE contato_id = c.id AND presente = 1) AS ultima_vez,
                      (SELECT COUNT(*) FROM presencas WHERE contato_id = c.id AND presente = 1) AS total_presencas
               FROM contatos c
               WHERE c.id NOT IN (
                   SELECT DISTINCT contato_id FROM presencas
                   WHERE presente = 1 AND data >= ? AND data <= ?
               )""",
            conn, params=(data_ini_iso, data_fim_iso),
        )
    hoje = date.today()
    def _dias(iso):
        if not iso:
            return None
        try:
            return (hoje - datetime.strptime(iso, "%Y-%m-%d").date()).days
        except Exception:
            return None
    df["dias_sem_vir"] = df["ultima_vez"].apply(_dias)
    return df


# --------------------- Aniversariantes ------------------
def aniversariantes_do_mes(mes=None):
    mes = mes or date.today().month
    with conn_db() as conn:
        df = pd.read_sql_query(
            "SELECT id, nome, data_nascimento, telefone FROM contatos "
            "WHERE data_nascimento IS NOT NULL AND data_nascimento != ''",
            conn,
        )
    if df.empty:
        return df
    def _parte(s, i):
        try:
            return int(s.split("-")[i])
        except Exception:
            return 0
    df["mes"] = df["data_nascimento"].apply(lambda x: _parte(x, 1))
    df["dia"] = df["data_nascimento"].apply(lambda x: _parte(x, 2))
    return df[df["mes"] == mes].sort_values("dia")[["id", "dia", "nome", "telefone"]]


def proximo_evento():
    hoje = date.today().isoformat()
    with conn_db() as conn:
        return conn.execute(
            "SELECT data, titulo, tipo FROM eventos WHERE data >= ? ORDER BY data ASC LIMIT 1",
            (hoje,),
        ).fetchone()


# =========================================================
# RELATÓRIO MENSAL (HTML)
# =========================================================
def gerar_relatorio_html(ano, mes):
    nomes_meses = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    nome_mes = nomes_meses[mes - 1]
    ini = f"{ano}-{mes:02d}-01"
    fim = f"{ano+1}-01-01" if mes == 12 else f"{ano}-{mes+1:02d}-01"

    with conn_db() as conn:
        grupos = conn.execute(
            "SELECT DISTINCT data FROM presencas WHERE presente = 1 AND data >= ? AND data < ? ORDER BY data",
            (ini, fim),
        ).fetchall()
        total_grupos = len(grupos)
        total_jovens = conn.execute("SELECT COUNT(*) FROM contatos").fetchone()[0]

        df_freq = pd.read_sql_query(
            """SELECT c.nome,
                      COALESCE(SUM(CASE WHEN p.presente = 1 THEN 1 ELSE 0 END), 0) AS p
               FROM contatos c
               LEFT JOIN presencas p ON p.contato_id = c.id AND p.data >= ? AND p.data < ?
               GROUP BY c.id, c.nome
               ORDER BY p DESC, c.nome COLLATE NOCASE""",
            conn, params=(ini, fim),
        )
        novos = pd.read_sql_query(
            """SELECT nome, telefone FROM contatos
               WHERE substr(criado_em, 1, 10) >= ? AND substr(criado_em, 1, 10) < ?""",
            conn, params=(ini, fim),
        )
        sumidos = pd.read_sql_query(
            """SELECT c.nome, c.telefone, MAX(p2.data) AS ultima
               FROM contatos c
               LEFT JOIN presencas p2 ON p2.contato_id = c.id AND p2.presente = 1
               WHERE c.id NOT IN (
                   SELECT contato_id FROM presencas WHERE presente = 1 AND data >= ? AND data < ?
               )
               AND EXISTS (SELECT 1 FROM presencas WHERE contato_id = c.id AND presente = 1)
               GROUP BY c.id, c.nome, c.telefone
               ORDER BY ultima ASC""",
            conn, params=(ini, fim),
        )

    media = df_freq["p"].mean() if not df_freq.empty else 0
    assiduos = int((df_freq["p"] / total_grupos * 100 >= 75).sum()) if total_grupos > 0 else 0
    presentes_alguma = int((df_freq["p"] > 0).sum())

    def linhas_freq():
        out = []
        for _, r in df_freq.iterrows():
            pct = (r["p"] / total_grupos * 100) if total_grupos > 0 else 0
            cor = "#22c55e" if pct >= 75 else ("#f59e0b" if pct >= 50 else ("#ef4444" if pct > 0 else "#9ca3af"))
            out.append(f"""
              <tr>
                <td style="padding:6px 10px;border-bottom:1px solid #eee">{r['nome']}</td>
                <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center">{int(r['p'])}</td>
                <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;color:{cor};font-weight:600">{pct:.0f}%</td>
              </tr>""")
        return "".join(out)

    def linhas_lista(df, cols):
        if df.empty:
            return "<tr><td colspan='3' style='padding:10px;color:#888'>—</td></tr>"
        out = []
        for _, r in df.iterrows():
            tds = "".join(f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{r[c] or '—'}</td>" for c in cols)
            out.append(f"<tr>{tds}</tr>")
        return "".join(out)

    datas_grupos = ", ".join(fmt_data(r[0]) for r in grupos) if grupos else "—"

    return f"""<!DOCTYPE html>
<html lang="pt-br"><head><meta charset="utf-8"><title>Relatório O.P.A — {nome_mes}/{ano}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#f7f7f9; color:#1f2937; padding:32px; max-width:900px; margin:auto; }}
  h1 {{ margin:0 0 6px 0; font-size:26px; }}
  .sub {{ color:#6b7280; margin-bottom:24px; }}
  .cards {{ display:flex; gap:12px; margin-bottom:24px; flex-wrap:wrap; }}
  .card {{ flex:1; min-width:150px; background:#fff; border-radius:10px; padding:16px; box-shadow:0 1px 3px rgba(0,0,0,0.05); }}
  .card .v {{ font-size:24px; font-weight:700; }}
  .card .l {{ font-size:12px; color:#6b7280; text-transform:uppercase; letter-spacing:0.5px; }}
  h2 {{ font-size:18px; margin-top:32px; margin-bottom:12px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.05); }}
  th {{ text-align:left; padding:10px; background:#f3f4f6; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; color:#6b7280; }}
</style></head>
<body>
  <h1>🙌 Relatório O.P.A</h1>
  <div class="sub">{nome_mes} de {ano} · Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</div>

  <div class="cards">
    <div class="card"><div class="l">Grupos no mês</div><div class="v">{total_grupos}</div></div>
    <div class="card"><div class="l">Jovens cadastrados</div><div class="v">{total_jovens}</div></div>
    <div class="card"><div class="l">Compareceram</div><div class="v">{presentes_alguma}</div></div>
    <div class="card"><div class="l">Assíduos (≥75%)</div><div class="v">{assiduos}</div></div>
    <div class="card"><div class="l">Frequência média</div><div class="v">{media:.0f}%</div></div>
  </div>

  <h2>📅 Grupos do mês</h2>
  <p style="color:#4b5563;margin:0">{datas_grupos}</p>

  <h2>📈 Frequência por jovem</h2>
  <table>
    <thead><tr><th>Nome</th><th style="text-align:center">Presenças</th><th style="text-align:center">%</th></tr></thead>
    <tbody>{linhas_freq()}</tbody>
  </table>

  <h2>🆕 Novos no mês ({len(novos)})</h2>
  <table>
    <thead><tr><th>Nome</th><th>Telefone</th><th></th></tr></thead>
    <tbody>{linhas_lista(novos, ['nome', 'telefone'])}</tbody>
  </table>

  <h2>🚶 Sumidos no mês ({len(sumidos)})</h2>
  <p style="color:#6b7280;font-size:13px;margin-top:-4px">Jovens com histórico que não vieram nenhum grupo em {nome_mes}.</p>
  <table>
    <thead><tr><th>Nome</th><th>Telefone</th><th>Última vez</th></tr></thead>
    <tbody>{linhas_lista(sumidos.rename(columns={'ultima':'ultima_fmt'}).assign(
        ultima_fmt=lambda d: d['ultima'].apply(fmt_data)), ['nome','telefone','ultima_fmt'])}</tbody>
  </table>

  <p style="margin-top:32px;color:#9ca3af;font-size:12px">💡 Para gerar PDF: abra este arquivo no navegador e use <b>Ctrl+P → Salvar como PDF</b>.</p>
</body></html>"""


# =========================================================
# LOGIN
# =========================================================
def tela_login():
    st.title("🙌 O.P.A · Área dos Coordenadores")
    st.caption("Acesso restrito. Fale com a coordenação se precisar da senha.")
    with st.form("login"):
        senha = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar", use_container_width=True)
    if ok:
        if senha == SENHA_COORD:
            st.session_state["auth"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")


# =========================================================
# NAVEGAÇÃO
# =========================================================
def ir_para(pagina, **kwargs):
    st.session_state["_ir_para"] = {"pagina": pagina, **kwargs}
    st.rerun()


def flash(msg, tipo="success"):
    st.session_state["_flash"] = (msg, tipo)


# =========================================================
# PÁGINA: HOME
# =========================================================
def pagina_home():
    st.markdown(f"## {saudacao_por_hora()}, Coordenação 👋")
    st.caption(fmt_data_extenso(date.today()))
    st.write("")

    st.markdown("###### ⚡ Ações rápidas")
    a1, a2, a3, a4 = st.columns(4)
    if a1.button("➕ Novo contato", use_container_width=True):
        st.session_state["pagina"] = "👥 Contatos"
        st.session_state["contatos_sub"] = "novo"
        st.rerun()
    if a2.button("📋 Registrar presença", use_container_width=True):
        st.session_state["pagina"] = "📋 Lista de Presença"
        st.session_state["presenca_sub"] = "selecao"
        st.rerun()
    if a3.button("📅 Novo evento", use_container_width=True):
        ir_para("📅 Agenda")
    if a4.button("📈 Ver frequência", use_container_width=True):
        ir_para("📈 Frequência")

    st.write("")

    sumidos, nunca, novos = resumo_alertas()
    if sumidos > 0 or novos > 0:
        col_a1, col_a2 = st.columns(2)
        with col_a1:
            if sumidos > 0:
                with st.container(border=True):
                    st.markdown(f"### 🚨 **{sumidos}** jovem(ns) não vêm há 30+ dias")
                    st.caption("Vale um toque no WhatsApp pra saber como estão.")
                    if st.button("Ver quem tá sumido", key="home_ver_sumidos", use_container_width=True):
                        ir_para("📈 Frequência")
            else:
                with st.container(border=True):
                    st.markdown("### 🎉 Ninguém sumido nos últimos 30 dias")
                    st.caption("Todo mundo que já veio, veio recentemente. 👏")
        with col_a2:
            if novos > 0:
                with st.container(border=True):
                    st.markdown(f"### 🆕 **{novos}** jovem(ns) novo(s) este mês")
                    st.caption("Cadastrados nos últimos 30 dias.")
                    if st.button("Ver os novos", key="home_ver_novos", use_container_width=True):
                        st.session_state["pagina"] = "👥 Contatos"
                        st.session_state["contatos_sub"] = "lista"
                        st.rerun()
            else:
                with st.container(border=True):
                    st.markdown("### 🆕 Sem novos cadastros este mês")
                    st.caption("Nenhum jovem novo nos últimos 30 dias.")
        if nunca > 0:
            st.caption(f"👀 Lembrete: **{nunca}** jovem(ns) cadastrado(s) nunca compareceu(ram).")

    st.write("")
    st.divider()

    total_jovens = contar_contatos()
    data_ult, presentes_ult, _ = ultimo_evento_com_presenca()
    eventos_mes = contar_eventos_do_mes()
    anivs = aniversariantes_do_mes()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("👥 Jovens cadastrados", total_jovens)
    if data_ult:
        m2.metric("✅ Presentes no último grupo", presentes_ult,
                  help=f"Último registro: {fmt_data(data_ult)}")
    else:
        m2.metric("✅ Presentes no último grupo", "—")
    m3.metric("📅 Eventos este mês", eventos_mes)
    m4.metric("🎂 Aniversariantes do mês", len(anivs))

    st.write("")
    st.divider()

    prox = proximo_evento()
    if prox:
        data_iso, titulo, tipo = prox[0], prox[1], (prox[2] if len(prox) > 2 else "grupo")
        d = datetime.strptime(data_iso, "%Y-%m-%d").date()
        dias = (d - date.today()).days
        if dias == 0:
            quando = "é **HOJE**! 🎉"
        elif dias == 1:
            quando = "é **amanhã**"
        elif dias < 0:
            quando = f"foi há {-dias} dia(s)"
        else:
            quando = f"em **{dias} dias**"
        tipo_info = tipo_evento_info(tipo)
        with st.container(border=True):
            st.markdown(
                f"### 📌 Próximo evento · {tipo_info['label']}\n"
                f"**{titulo or 'Grupo O.P.A'}** — {nome_dia_semana(data_iso)}, "
                f"{fmt_data(data_iso)} ({quando})"
            )
            if st.button("📋 Abrir lista de presença do dia", key="home_abrir_prox"):
                st.session_state["pagina"] = "📋 Lista de Presença"
                st.session_state["presenca_sub"] = "lista"
                st.session_state["presenca_evento_data"] = data_iso
                st.rerun()
    else:
        with st.container(border=True):
            st.markdown(
                "### 📌 Próximo grupo\n"
                "_Nenhum evento agendado. Vá em **📅 Agenda** pra criar um._"
            )

    st.write("")

    df_ult = dados_ultimos_grupos(4)
    if not df_ult.empty:
        st.markdown("##### 📊 Presença nos últimos grupos")
        df_ult = df_ult.copy()
        df_ult["label"] = df_ult["data"].apply(lambda x: fmt_data(x)[:5])
        df_ult["dow"] = df_ult["data"].apply(nome_dia_semana)
        chart_ult = (
            alt.Chart(df_ult)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("label:N", sort=list(df_ult["label"]), title=None,
                        axis=alt.Axis(labelAngle=0)),
                y=alt.Y("presentes:Q", title="Presentes"),
                color=alt.value("#7c5cff"),
                tooltip=[
                    alt.Tooltip("dow:N", title="Dia"),
                    alt.Tooltip("label:N", title="Data"),
                    alt.Tooltip("presentes:Q", title="Presentes"),
                    alt.Tooltip("total_cadastrado:Q", title="Total cadastrado"),
                    alt.Tooltip("percentual:Q", title="%", format=".1f"),
                ],
            ).properties(height=180)
        )
        linha_total = (
            alt.Chart(pd.DataFrame({"y": [df_ult["total_cadastrado"].iloc[0]]}))
            .mark_rule(strokeDash=[4, 4], color="#9ca3af")
            .encode(y="y:Q")
        )
        st.altair_chart(chart_ult + linha_total, use_container_width=True)
        st.caption(f"A linha pontilhada é o total de cadastrados ({df_ult['total_cadastrado'].iloc[0]}).")

    st.write("")
    st.divider()

    with st.expander("📄 Gerar relatório do mês"):
        st.caption("Gera um HTML bonito pra você salvar como PDF (Ctrl+P no navegador).")
        hoje = date.today()
        col_m, col_a, col_btn = st.columns([1, 1, 2])
        with col_m:
            mes_sel = st.selectbox("Mês", options=list(range(1, 13)),
                                   index=hoje.month - 1,
                                   format_func=lambda m: ["Janeiro", "Fevereiro", "Março",
                                                          "Abril", "Maio", "Junho", "Julho",
                                                          "Agosto", "Setembro", "Outubro",
                                                          "Novembro", "Dezembro"][m - 1])
        with col_a:
            ano_sel = st.selectbox("Ano", options=list(range(hoje.year, hoje.year - 5, -1)))
        with col_btn:
            st.write("")
            st.write("")
            gerar = st.button("📄 Gerar relatório", type="primary", use_container_width=True)

        if gerar:
            html = gerar_relatorio_html(ano_sel, mes_sel)
            nome_meses = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho",
                          "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
            st.download_button(
                "📥 Baixar relatório (.html)",
                data=html.encode("utf-8"),
                file_name=f"relatorio_opa_{nome_meses[mes_sel-1]}_{ano_sel}.html",
                mime="text/html",
                type="primary",
                use_container_width=False,
            )
            st.caption("Depois de baixar: abra o arquivo no navegador → **Ctrl+P** → **Salvar como PDF**.")

    col_esq, col_dir = st.columns(2)
    with col_esq:
        with st.container(border=True):
            st.markdown("#### 🔜 Próximos eventos")
            df_prox = listar_eventos("futuros").head(5)
            if df_prox.empty:
                st.caption("Sem eventos para os próximos dias.")
            else:
                for _, ev in df_prox.iterrows():
                    presentes, total = estatisticas_evento(ev["data"])
                    tipo_info = tipo_evento_info(ev.get("tipo"))
                    linha1, linha2 = st.columns([3, 1])
                    linha1.markdown(
                        f"**{fmt_data(ev['data'])}** · {ev['titulo'] or '_sem título_'} "
                        f"<small style='color:gray'>{tipo_info['label']}</small>",
                        unsafe_allow_html=True,
                    )
                    linha2.caption(f"👥 {presentes}/{total}")
                    st.caption(f"<small>{nome_dia_semana(ev['data'])}</small>", unsafe_allow_html=True)
                    st.divider()

    with col_dir:
        with st.container(border=True):
            st.markdown("#### 🎂 Aniversariantes do mês")
            if anivs.empty:
                st.caption("Ninguém faz aniversário este mês.")
            else:
                hoje = date.today()
                for _, row in anivs.iterrows():
                    dia = row["dia"]
                    destaque = " 🎉 **HOJE!**" if dia == hoje.day else ""
                    st.markdown(
                        f"**{dia:02d}/{hoje.month:02d}** — {row['nome']} · "
                        f"{row['telefone'] or '—'}{destaque}"
                    )
                    st.divider()
                if st.button("Ver todos os aniversariantes", use_container_width=True):
                    ir_para("🎂 Aniversariantes")

    st.write("")

    col_novos, col_ult = st.columns(2)
    with col_novos:
        with st.container(border=True):
            st.markdown("#### 🆕 Novos nos últimos 30 dias")
            df_nov = novos_recentes(30, 5)
            if df_nov.empty:
                st.caption("Nenhum jovem cadastrado nos últimos 30 dias.")
            else:
                for _, row in df_nov.iterrows():
                    criado = row["criado_em"][:10] if row["criado_em"] else "—"
                    st.markdown(
                        f"- **{row['nome']}** · {row['telefone'] or '—'} "
                        f"<small style='color:gray'>({fmt_data(criado)})</small>",
                        unsafe_allow_html=True,
                    )

    with col_ult:
        with st.container(border=True):
            st.markdown("#### 👋 Últimos cadastrados")
            ult = ultimos_contatos(5)
            if ult.empty:
                st.caption("Nenhum contato cadastrado ainda.")
            else:
                for _, row in ult.iterrows():
                    criado = row["criado_em"][:10] if row["criado_em"] else "—"
                    st.markdown(
                        f"- **{row['nome']}** · {row['telefone'] or '—'} "
                        f"<small style='color:gray'>({fmt_data(criado)})</small>",
                        unsafe_allow_html=True,
                    )


# =========================================================
# PÁGINA: LISTA DE PRESENÇA
# =========================================================
def pagina_presenca():
    sub = st.session_state.get("presenca_sub", "selecao")
    if sub == "lista" and st.session_state.get("presenca_evento_data"):
        _render_lista_presenca()
    else:
        _render_selecao_evento()


def _render_selecao_evento():
    st.header("📋 Lista de Presença")
    st.caption("Escolha o evento para registrar ou consultar a presença.")

    filtro_tipo = st.radio(
        "Tipo de evento",
        ["Todos", "Grupos", "Retiros", "Especiais", "Outros"],
        horizontal=True,
        key="pres_filtro_tipo",
    )
    mapa_filtro = {
        "Todos": None, "Grupos": "grupo", "Retiros": "retiro",
        "Especiais": "especial", "Outros": "outro",
    }
    tipo_db = mapa_filtro[filtro_tipo]

    df = listar_eventos("todos", tipo=tipo_db)
    if df.empty:
        st.info("Nenhum evento encontrado com esse filtro.")
        return

    hoje_iso = date.today().isoformat()
    df_prox = df[df["data"] >= hoje_iso].sort_values("data")
    df_pass = df[df["data"] < hoje_iso].sort_values("data", ascending=False)

    tab_p, tab_pass = st.tabs([
        f"🔜 Próximos ({len(df_prox)})",
        f"📜 Passados ({len(df_pass)})",
    ])

    with tab_p:
        if df_prox.empty:
            st.info("Sem eventos futuros nesse filtro.")
        else:
            for _, ev in df_prox.iterrows():
                _card_selecao_evento(ev, ctx="prox")

    with tab_pass:
        if df_pass.empty:
            st.info("Sem eventos passados nesse filtro.")
        else:
            for _, ev in df_pass.iterrows():
                _card_selecao_evento(ev, ctx="pass")


def _card_selecao_evento(ev, ctx):
    presentes, total = estatisticas_evento(ev["data"])
    tipo_info = tipo_evento_info(ev.get("tipo"))
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
        c1.markdown(
            f"**{fmt_data(ev['data'])}**  \n<small>{nome_dia_semana(ev['data'])}</small>",
            unsafe_allow_html=True,
        )
        c2.markdown(
            f"**{ev['titulo'] or '_Sem título_'}**  \n"
            f"<small style='color:{tipo_info['cor']}'>{tipo_info['label']}</small>",
            unsafe_allow_html=True,
        )
        c3.markdown(f"👥 **{presentes}/{total}**")
        if c4.button("📋 Abrir lista", key=f"abrir_{ctx}_{ev['id']}", use_container_width=True):
            st.session_state["presenca_sub"] = "lista"
            st.session_state["presenca_evento_data"] = ev["data"]
            st.rerun()


def _ordenar_contatos(df, ordem):
    if ordem == "Nome (A-Z)":
        return df.sort_values("nome", key=lambda s: s.str.lower())
    if ordem == "Nome (Z-A)":
        return df.sort_values("nome", key=lambda s: s.str.lower(), ascending=False)
    if ordem == "Mais assíduos primeiro":
        mapa = mapa_assiduidade()
        df = df.copy()
        df["_assid"] = df["id"].map(mapa).fillna(0).astype(int)
        return df.sort_values(["_assid", "nome"], ascending=[False, True]).drop(columns="_assid")
    if ordem == "Aniversariantes do mês primeiro":
        hoje = date.today()
        def _is_aniv(iso):
            if not iso:
                return 0
            try:
                return 1 if int(iso.split("-")[1]) == hoje.month else 0
            except Exception:
                return 0
        df = df.copy()
        df["_aniv"] = df["data_nascimento"].apply(_is_aniv)
        return df.sort_values(["_aniv", "nome"], ascending=[False, True]).drop(columns="_aniv")
    return df


def _render_lista_presenca():
    data_iso = st.session_state["presenca_evento_data"]
    data_sel = datetime.strptime(data_iso, "%Y-%m-%d").date()

    if st.button("← Voltar para a lista de eventos"):
        st.session_state["presenca_sub"] = "selecao"
        st.session_state.pop("presenca_evento_data", None)
        st.rerun()

    with conn_db() as conn:
        ev = conn.execute(
            "SELECT titulo, observacoes, tipo FROM eventos WHERE data = ?", (data_iso,)
        ).fetchone()

    titulo_ev = (ev[0] if ev and ev[0] else "Grupo O.P.A")
    tipo_ev = (ev[2] if ev and len(ev) > 2 and ev[2] else "grupo")
    tipo_info = tipo_evento_info(tipo_ev)

    st.markdown(f"## 📋 {titulo_ev}")
    st.caption(f"📅 {nome_dia_semana(data_iso)}, {data_sel.strftime('%d/%m/%Y')} · {tipo_info['label']}")
    if ev and ev[1]:
        st.caption(f"📝 {ev[1]}")

    presentes_total, total_contatos = estatisticas_evento(data_iso)
    ausentes_total = max(total_contatos - presentes_total, 0)

    r1, r2, r3 = st.columns(3)
    r1.metric("✅ Presentes", presentes_total)
    r2.metric("❌ Ausentes", ausentes_total)
    r3.metric("👥 Total cadastrado", total_contatos)

    if ausentes_total > 0:
        col_pop, _ = st.columns([1, 3])
        with col_pop:
            with st.popover(f"👀 Ver quem faltou ({ausentes_total})", use_container_width=True):
                df_aus = ausentes_do_dia(data_iso)
                for _, row in df_aus.iterrows():
                    st.markdown(f"- **{row['nome']}** · {row['telefone'] or '—'}")

    st.divider()

    todos = listar_contatos()
    if todos.empty:
        st.info("Nenhum contato cadastrado. Vá em **👥 Contatos → ➕ Novo Contato**.")
        return

    col_b, col_o = st.columns([2, 1])
    with col_b:
        opcoes = ["(mostrar todos)"] + todos["nome"].tolist()
        busca = st.selectbox(
            "🔍 Buscar jovem (digite pra ver sugestões)",
            options=opcoes, index=0,
            key=f"busca_pres_{data_iso}",
            placeholder="Digite o nome...",
        )
    with col_o:
        ordem = st.selectbox(
            "Ordenar por",
            ["Nome (A-Z)", "Nome (Z-A)", "Mais assíduos primeiro", "Aniversariantes do mês primeiro"],
            key=f"ordem_pres_{data_iso}",
        )

    if busca == "(mostrar todos)":
        contatos = todos.copy()
    else:
        contatos = todos[todos["nome"] == busca].copy()

    contatos = _ordenar_contatos(contatos, ordem)

    ids_visiveis = contatos["id"].tolist()
    col_m1, col_m2, _ = st.columns([1, 1, 3])
    if col_m1.button("✅ Marcar todos presentes", use_container_width=True,
                     key=f"marcar_todos_{data_iso}_{len(ids_visiveis)}"):
        marcar_todos_presentes(data_iso, ids_visiveis, True)
        flash("Todos marcados como presentes.", "success")
        st.rerun()
    if col_m2.button("❌ Desmarcar todos", use_container_width=True,
                     key=f"desmarcar_todos_{data_iso}_{len(ids_visiveis)}"):
        marcar_todos_presentes(data_iso, ids_visiveis, False)
        flash("Todos desmarcados.", "success")
        st.rerun()

    presencas = presencas_do_dia(data_iso)
    st.caption(f"Mostrando **{len(contatos)}** contato(s). Salva automático ao marcar.")

    h1, h2, h3, h4 = st.columns([3, 2, 2, 1])
    h1.markdown("**Nome**")
    h2.markdown("**Nascimento**")
    h3.markdown("**Telefone**")
    h4.markdown("**Presente**")

    for _, row in contatos.iterrows():
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.write(row["nome"])
        c2.write(fmt_data(row["data_nascimento"]))
        c3.write(row["telefone"] or "—")
        atual = bool(presencas.get(row["id"], False))
        marcado = c4.checkbox(
            "presente", value=atual,
            key=f"pres_{data_iso}_{row['id']}",
            label_visibility="collapsed",
        )
        if marcado != atual:
            salvar_presenca(row["id"], data_iso, marcado)
            st.rerun()


# =========================================================
# CALENDÁRIO
# =========================================================
def render_calendario():
    if not HAS_CALENDAR:
        st.warning("Instale o componente:\n\n```\npip install streamlit-calendar\n```")
        return

    with conn_db() as conn:
        df = pd.read_sql_query("SELECT * FROM eventos ORDER BY data", conn)

    hoje_iso = date.today().isoformat()
    events = []
    for _, ev in df.iterrows():
        presentes, total = estatisticas_evento(ev["data"])
        titulo = ev["titulo"] or "Grupo O.P.A"
        bg = cor_evento(ev, hoje_iso)
        tipo_info = tipo_evento_info(ev.get("tipo"))
        events.append({
            "id": ev["data"],
            "title": f"{tipo_info['label'].split()[0]} {titulo} · {presentes}/{total}",
            "start": ev["data"],
            "allDay": True,
            "backgroundColor": bg,
            "borderColor": bg,
            "textColor": "#ffffff",
            "extendedProps": {"evento_id": int(ev["id"]), "data": ev["data"]},
        })

    calendar_options = {
        "locale": "pt-br",
        "initialView": "dayGridMonth",
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "dayGridMonth,listMonth",
        },
        "buttonText": {"today": "Hoje", "month": "Mês", "list": "Lista"},
        "height": 700, "firstDay": 0, "editable": False, "selectable": False,
        "dayMaxEvents": 3, "displayEventTime": False, "eventDisplay": "block",
    }

    state = calendar(events=events, options=calendar_options, key="opa_calendar")

    if state:
        ev_click = state.get("eventClick")
        if ev_click:
            ev_obj = ev_click.get("event", {}) if isinstance(ev_click, dict) else {}
            data_click = ev_obj.get("id") or (ev_obj.get("extendedProps") or {}).get("data")
            if data_click:
                st.session_state["pagina"] = "📋 Lista de Presença"
                st.session_state["presenca_sub"] = "lista"
                st.session_state["presenca_evento_data"] = data_click
                st.rerun()

    st.caption("💡 **Clique em um evento** pra abrir a lista de presença daquele dia.")


# =========================================================
# PÁGINA: AGENDA
# =========================================================
def _card_evento(ev, ctx):
    presentes, total = estatisticas_evento(ev["data"])
    eid = ev["id"]
    tipo_info = tipo_evento_info(ev.get("tipo"))
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
        c1.markdown(
            f"**{fmt_data(ev['data'])}**  \n<small>{nome_dia_semana(ev['data'])}</small>",
            unsafe_allow_html=True,
        )
        c2.markdown(
            f"**{ev['titulo'] or '_(sem título)_'}**  \n"
            f"<small style='color:{tipo_info['cor']}'>{tipo_info['label']}</small>",
            unsafe_allow_html=True,
        )
        c3.markdown(f"👥 **{presentes}/{total}**")

        if c4.button("📋 Abrir lista", key=f"abrir_{ctx}_{eid}", use_container_width=True):
            st.session_state["pagina"] = "📋 Lista de Presença"
            st.session_state["presenca_sub"] = "lista"
            st.session_state["presenca_evento_data"] = ev["data"]
            st.rerun()

        if ev.get("observacoes"):
            st.caption(f"📝 {ev['observacoes']}")

        with st.expander("✏️ Editar / Remover"):
            with st.form(f"edit_ev_{ctx}_{eid}"):
                novo_titulo = st.text_input("Título", value=ev["titulo"] or "", key=f"tit_{ctx}_{eid}")
                tipo_atual = ev.get("tipo") or "grupo"
                idx_tipo = list(TIPOS_EVENTO.keys()).index(tipo_atual) if tipo_atual in TIPOS_EVENTO else 0
                novo_tipo = st.selectbox(
                    "Tipo",
                    options=list(TIPOS_EVENTO.keys()),
                    format_func=lambda x: TIPOS_EVENTO[x]["label"],
                    index=idx_tipo,
                    key=f"tipo_{ctx}_{eid}",
                )
                novas_obs = st.text_area("Observações", value=ev["observacoes"] or "", key=f"obs_{ctx}_{eid}")
                salvar = st.form_submit_button("Salvar alterações", use_container_width=True)
            if salvar:
                atualizar_evento(eid, novo_titulo, novas_obs, tipo=novo_tipo)
                flash("Evento atualizado.")
                st.rerun()

            if st.button("🗑️ Remover evento", key=f"del_{ctx}_{eid}"):
                deletar_evento(eid)
                flash("Evento removido.")
                st.rerun()


def pagina_agenda():
    st.header("📅 Agenda do Grupo")
    st.caption("Agende os próximos encontros e volte a qualquer dia pra ver quem foi.")

    with st.expander("➕ Agendar novo evento"):
        with st.form("form_evento", clear_on_submit=True):
            data_ev = st.date_input("Data", value=date.today(), format="DD/MM/YYYY")
            tipo_ev = st.selectbox(
                "Tipo de evento",
                options=list(TIPOS_EVENTO.keys()),
                format_func=lambda x: TIPOS_EVENTO[x]["label"],
                index=0,
            )
            titulo = st.text_input("Título (opcional)", placeholder="Ex: Retiro de inverno")
            obs = st.text_area("Observações (opcional)")
            salvar = st.form_submit_button("Salvar evento", use_container_width=True)
        if salvar:
            ok, msg = criar_evento(data_ev.isoformat(), titulo, obs, tipo=tipo_ev)
            flash(msg, "success" if ok else "error")
            if ok:
                st.rerun()

    st.divider()

    col_f, _ = st.columns([1, 3])
    with col_f:
        filtro_tipo = st.radio(
            "Filtrar por tipo",
            OPCOES_TIPO_FILTRO,
            horizontal=True,
            key="agenda_filtro_tipo",
        )
    mapa_filtro = {
        "Todos": None, "Grupos": "grupo", "Retiros": "retiro",
        "Especiais": "especial", "Outros": "outro",
    }
    tipo_db = mapa_filtro[filtro_tipo]

    tab_cal, tab_prox, tab_pass, tab_todos = st.tabs([
        "🗓️ Calendário", "🔜 Próximos", "📜 Passados", "📋 Todos"
    ])

    with tab_cal:
        render_calendario()

    with tab_prox:
        df = listar_eventos("futuros", tipo=tipo_db)
        if df.empty:
            st.info("Nenhum evento agendado nesse filtro.")
        else:
            for _, ev in df.iterrows():
                _card_evento(ev, ctx="prox")

    with tab_pass:
        df = listar_eventos("passados", tipo=tipo_db)
        if df.empty:
            st.info("Nenhum evento passado nesse filtro.")
        else:
            for _, ev in df.iterrows():
                _card_evento(ev, ctx="pass")

    with tab_todos:
        df = listar_eventos("todos", tipo=tipo_db)
        if df.empty:
            st.info("Nenhum evento nesse filtro.")
        else:
            for _, ev in df.iterrows():
                _card_evento(ev, ctx="todos")


# =========================================================
# PÁGINA: FREQUÊNCIA
# =========================================================
def _faixa_freq(p):
    if p >= 75:
        return "Alta (≥75%)"
    if p >= 50:
        return "Média (50–74%)"
    if p >= 1:
        return "Baixa (<50%)"
    return "Nunca veio"


def render_heatmap(ano):
    st.markdown(f"##### 🗓️ Heatmap jovem × mês — {ano}")
    st.caption("Cada linha é um jovem, cada coluna é um mês. Cor = % de presença naquele mês.")

    df_heat = dados_heatmap(ano)
    if df_heat.empty or df_heat["total_mes"].sum() == 0:
        st.info(f"Sem dados de presença em **{ano}**.")
        return

    mostrar_zerados = st.checkbox(
        "Mostrar quem nunca veio no ano", value=False, key="heat_zerados",
    )
    df_heat = df_heat.copy()
    if not mostrar_zerados:
        ativos = df_heat[df_heat["total_ano"] > 0]["nome"].unique()
        df_heat = df_heat[df_heat["nome"].isin(ativos)]

    if df_heat.empty:
        st.info("Nenhum jovem com presença registrada neste ano.")
        return

    nomes_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                   "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    df_heat["mes_nome"] = df_heat["mes"].apply(lambda x: nomes_meses[x - 1])

    ordem_nomes = (
        df_heat.groupby("nome")["total_ano"].max()
        .sort_values(ascending=False).index.tolist()
    )

    df_sem = df_heat[df_heat["total_mes"] == 0]
    df_com = df_heat[df_heat["total_mes"] > 0]

    base_enc = dict(
        x=alt.X("mes_nome:N", sort=nomes_meses, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("nome:N", sort=ordem_nomes, title=None, axis=alt.Axis(labelLimit=250)),
    )

    rect_sem = alt.Chart(df_sem).mark_rect(color="#374151", stroke="#1f2937").encode(**base_enc)
    rect_com = alt.Chart(df_com).mark_rect(stroke="#1f2937").encode(
        **base_enc,
        color=alt.Color(
            "percentual:Q",
            scale=alt.Scale(domain=[0, 50, 100], range=["#ef4444", "#f59e0b", "#22c55e"]),
            legend=alt.Legend(title="% de presença", orient="top"),
        ),
        tooltip=[
            alt.Tooltip("nome:N", title="Jovem"),
            alt.Tooltip("mes_nome:N", title="Mês"),
            alt.Tooltip("presencas:Q", title="Presenças"),
            alt.Tooltip("total_mes:Q", title="Grupos no mês"),
            alt.Tooltip("percentual:Q", title="%", format=".1f"),
        ],
    )
    texto = alt.Chart(df_com).mark_text(fontSize=10, color="white").encode(
        **base_enc, text=alt.Text("presencas:Q"),
    )
    chart = (rect_sem + rect_com + texto).properties(height=max(280, len(ordem_nomes) * 24))
    with st.container(height=600, border=True):
        st.altair_chart(chart, use_container_width=True)
    st.caption("⚫ Cinza = mês sem grupo. Números brancos = presenças do jovem no mês.")


def render_monitoramento():
    st.markdown("##### 🚨 Monitoramento — quem está sumindo?")
    st.caption("Filtre um período e veja quem não apareceu nos grupos recentes.")

    col1, _ = st.columns([1, 2])
    with col1:
        opcao = st.radio(
            "Período",
            ["Este mês", "Últimos 30 dias", "Últimos 60 dias", "Últimos 90 dias"],
            key="mon_periodo",
        )

    data_ini, data_fim, texto_periodo = resolver_periodo(opcao)
    ini_iso, fim_iso = data_ini.isoformat(), data_fim.isoformat()

    with conn_db() as conn:
        grupos_no_periodo = conn.execute(
            "SELECT COUNT(DISTINCT data) FROM presencas WHERE presente = 1 AND data >= ? AND data <= ?",
            (ini_iso, fim_iso),
        ).fetchone()[0]

    df = jovens_em_alerta(ini_iso, fim_iso)
    total_jovens = contar_contatos()
    ausentes_periodo = len(df)
    presentes_periodo = total_jovens - ausentes_periodo

    m1, m2, m3 = st.columns(3)
    m1.metric(f"📅 Grupos {texto_periodo}", grupos_no_periodo)
    m2.metric("✅ Compareceram", presentes_periodo)
    m3.metric("😴 Não compareceram", ausentes_periodo)

    if grupos_no_periodo == 0:
        st.warning(f"⚠️ Nenhum grupo registrado {texto_periodo}.")
        return

    if df.empty:
        st.success(f"🎉 Todos compareceram {texto_periodo}!")
        return

    df_com_hist = df[df["total_presencas"] > 0].copy()
    df_sem_hist = df[df["total_presencas"] == 0].copy()

    tab_sumidos, tab_nunca = st.tabs([
        f"🚶 Sumiram ({len(df_com_hist)})",
        f"❄️ Nunca vieram ({len(df_sem_hist)})",
    ])

    with tab_sumidos:
        if df_com_hist.empty:
            st.info("Nenhum jovem com histórico ficou de fora. 👏")
        else:
            df_com_hist = df_com_hist.sort_values("dias_sem_vir", ascending=False)
            tabela = pd.DataFrame({
                "Nome": df_com_hist["nome"],
                "Telefone": df_com_hist["telefone"].fillna("—").replace("", "—"),
                "Última vez": df_com_hist["ultima_vez"].apply(fmt_data),
                "Sem vir há": df_com_hist["dias_sem_vir"].apply(fmt_dias),
                "Presenças": df_com_hist["total_presencas"],
            })
            tabela["WhatsApp"] = df_com_hist.apply(
                lambda r: link_whatsapp(r["telefone"], r["nome"]), axis=1,
            )
            st.dataframe(
                tabela, hide_index=True, use_container_width=True,
                column_config={
                    "WhatsApp": st.column_config.LinkColumn(
                        "WhatsApp", display_text="💬 Chamar", width="small",
                    )
                },
            )
            st.download_button(
                "📥 Baixar CSV",
                data=tabela.drop(columns=["WhatsApp"]).to_csv(index=False).encode("utf-8"),
                file_name=f"sumidos_{opcao.lower().replace(' ', '_')}.csv",
                mime="text/csv",
            )

    with tab_nunca:
        if df_sem_hist.empty:
            st.info("Todo mundo cadastrado já apareceu pelo menos uma vez. 🎉")
        else:
            df_sem_hist = df_sem_hist.copy()
            df_sem_hist["criado_em_dt"] = df_sem_hist["criado_em"].fillna("").str[:10]
            tabela = pd.DataFrame({
                "Nome": df_sem_hist["nome"],
                "Telefone": df_sem_hist["telefone"].fillna("—").replace("", "—"),
                "Cadastrado em": df_sem_hist["criado_em_dt"].apply(fmt_data),
            })
            tabela["WhatsApp"] = df_sem_hist.apply(
                lambda r: link_whatsapp(r["telefone"], r["nome"],
                                        template=f"Oi {r['nome'].split()[0]}! Tudo bem? "
                                                 f"Que bom ter você no grupo, apareça quando puder 💛"),
                axis=1,
            )
            st.dataframe(
                tabela, hide_index=True, use_container_width=True,
                column_config={
                    "WhatsApp": st.column_config.LinkColumn(
                        "WhatsApp", display_text="💬 Chamar", width="small",
                    )
                },
            )
            st.download_button(
                "📥 Baixar CSV",
                data=tabela.drop(columns=["WhatsApp"]).to_csv(index=False).encode("utf-8"),
                file_name="nunca_vieram.csv", mime="text/csv",
            )


def pagina_frequencia():
    st.header("📈 Frequência dos Jovens")
    st.caption("Visão geral da participação nos grupos.")

    hoje = date.today()
    anos_disponiveis = list(range(hoje.year, hoje.year - 5, -1))

    col_ano, _ = st.columns([1, 3])
    with col_ano:
        ano = st.selectbox("📆 Ano", options=anos_disponiveis, index=0)

    df, total_grupos = frequencia_por_ano(ano)

    if total_grupos == 0:
        st.info(f"📭 Nenhum grupo registrado em **{ano}**.")
        st.divider()
        render_heatmap(ano)
        st.divider()
        render_monitoramento()
        return

    media = df["percentual"].mean() if not df.empty else 0
    presentes_alguma = int((df["grupos_presentes"] > 0).sum())
    assiduos = int((df["percentual"] >= 75).sum())
    ausentes = int((df["grupos_presentes"] == 0).sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"📅 Grupos em {ano}", total_grupos)
    m2.metric("✅ Vieram pelo menos 1x", presentes_alguma)
    m3.metric("⭐ Assíduos (≥75%)", assiduos)
    m4.metric("👥 Frequência média", f"{media:.1f}%",
              help=f"{ausentes} jovem(ns) nunca vieram em {ano}")

    st.divider()

    col1, col2 = st.columns([2, 1])
    with col1:
        opcoes_busca = ["(mostrar todos)"] + sorted(df["nome"].tolist())
        busca = st.selectbox(
            "🔍 Buscar jovem (digite pra ver sugestões)",
            options=opcoes_busca, index=0, key="freq_busca",
        )
    with col2:
        min_freq = st.slider("Frequência mínima (%)", 0, 100, 0, step=5)

    df_f = df.copy()
    if busca != "(mostrar todos)":
        df_f = df_f[df_f["nome"] == busca]
    df_f = df_f[df_f["percentual"] >= min_freq]

    if df_f.empty:
        st.info("Nenhum jovem bate com os filtros.")
    else:
        df_f = df_f.copy()
        df_f["faixa"] = df_f["percentual"].apply(_faixa_freq)

        st.markdown("##### 📊 Percentual por jovem")
        ordem_faixas = ["Alta (≥75%)", "Média (50–74%)", "Baixa (<50%)", "Nunca veio"]
        cores = {
            "Alta (≥75%)": "#22c55e", "Média (50–74%)": "#f59e0b",
            "Baixa (<50%)": "#ef4444", "Nunca veio": "#6b7280",
        }
        chart_freq = (
            alt.Chart(df_f).mark_bar().encode(
                y=alt.Y("nome:N", sort="-x", title=None, axis=alt.Axis(labelLimit=250)),
                x=alt.X("percentual:Q", title="% de presença", scale=alt.Scale(domain=[0, 100])),
                color=alt.Color(
                    "faixa:N",
                    scale=alt.Scale(domain=ordem_faixas, range=[cores[f] for f in ordem_faixas]),
                    legend=alt.Legend(title=None, orient="top"),
                ),
                tooltip=[
                    alt.Tooltip("nome:N", title="Jovem"),
                    alt.Tooltip("grupos_presentes:Q", title="Grupos"),
                    alt.Tooltip("total_grupos:Q", title="Total do ano"),
                    alt.Tooltip("percentual:Q", title="%", format=".1f"),
                ],
            ).properties(height=max(300, len(df_f) * 24))
        )
        st.altair_chart(chart_freq, use_container_width=True)

        st.markdown("##### 📋 Detalhamento")
        tabela = df_f[["nome", "telefone", "grupos_presentes", "total_grupos", "percentual", "ultima_vez"]].copy()
        tabela["percentual"] = tabela["percentual"].apply(lambda x: f"{x:.1f}%")
        tabela["ultima_vez"] = tabela["ultima_vez"].apply(fmt_data)
        tabela = tabela.rename(columns={
            "nome": "Nome", "telefone": "Telefone", "grupos_presentes": "Presenças",
            "total_grupos": "Grupos no ano", "percentual": "%", "ultima_vez": "Última vez",
        })
        st.dataframe(tabela, hide_index=True, use_container_width=True)

    st.divider()
    st.markdown(f"##### 📅 Grupos realizados por mês — {ano}")
    df_mes = grupos_por_mes(ano)
    nomes_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                   "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    df_mes["nome_mes"] = df_mes["mes"].apply(lambda x: nomes_meses[int(x) - 1])

    chart_mes = (
        alt.Chart(df_mes)
        .mark_bar(color="#7c5cff", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("nome_mes:N", sort=nomes_meses, title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("grupos:Q", title="Quantidade de grupos"),
            tooltip=[alt.Tooltip("nome_mes:N", title="Mês"), alt.Tooltip("grupos:Q", title="Grupos")],
        ).properties(height=260)
    )
    st.altair_chart(chart_mes, use_container_width=True)

    st.divider()
    render_heatmap(ano)
    st.divider()
    render_monitoramento()


# =========================================================
# PÁGINA: CONTATOS
# =========================================================
def pagina_contatos():
    sub = st.session_state.get("contatos_sub", "lista")
    if sub == "novo":
        pagina_criar_contato()
    elif sub == "detalhe" and st.session_state.get("contato_atual_id"):
        pagina_detalhe_contato(st.session_state["contato_atual_id"])
    else:
        _render_lista_contatos()


def _render_lista_contatos():
    st.header("👥 Contatos")
    st.caption("Clique em um jovem pra ver todas as informações e a frequência dele.")

    col_novo, _ = st.columns([1, 4])
    with col_novo:
        if st.button("➕ Novo Contato", type="primary", use_container_width=True):
            st.session_state["contatos_sub"] = "novo"
            st.rerun()

    busca = st.text_input(
        "🔍 Buscar (nome, responsável ou telefone)",
        placeholder="Digite nome, responsável ou DDD+número...",
        key="busca_contatos",
    )
    df = listar_contatos(busca)

    if df.empty:
        st.info("Nenhum contato encontrado.")
        return

    st.caption(f"**{len(df)}** contato(s). Clique em **Abrir** pra ver detalhes.")

    for _, row in df.iterrows():
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        tem_obs = bool((row.get("observacoes") or "").strip())
        prefixo = "📝 " if tem_obs else ""
        c1.markdown(f"{prefixo}**{row['nome']}**")
        c2.markdown(f"📱 {row['telefone'] or '—'}")
        resp_nome = row.get("nome_responsavel") or ""
        resp_tel = row.get("telefone_responsavel") or ""
        if resp_nome or resp_tel:
            c3.markdown(f"👨‍👩‍👧 {resp_nome or '—'} · {resp_tel or '—'}")
        else:
            c3.markdown("<small style='color:gray'>sem responsável</small>", unsafe_allow_html=True)

        if c4.button("Abrir", key=f"abrir_contato_{row['id']}", use_container_width=True):
            st.session_state["contato_atual_id"] = int(row["id"])
            st.session_state["contatos_sub"] = "detalhe"
            st.rerun()

        st.divider()


def pagina_criar_contato():
    st.header("➕ Novo Contato")
    st.caption("Preencha os dados do jovem. Os campos com * são obrigatórios.")

    if st.button("← Voltar"):
        st.session_state["contatos_sub"] = "lista"
        st.rerun()

    if "novo_contato_versao" not in st.session_state:
        st.session_state["novo_contato_versao"] = 0
    v = st.session_state["novo_contato_versao"]

    st.markdown("##### 👤 Dados do jovem")
    nome = st.text_input("Nome completo do jovem *", key=f"novo_nome_{v}")
    if nome.strip():
        existente = nome_ja_existe(nome)
        if existente:
            st.warning(f"⚠️ Já existe um jovem com esse nome: **{existente}**. "
                       f"Verifique se não é a mesma pessoa.")

    col1, col2 = st.columns(2)
    with col1:
        nascimento = st.date_input(
            "Data de nascimento",
            value=date(2005, 1, 1),
            min_value=date(1940, 1, 1),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key=f"novo_nasc_{v}",
        )
    with col2:
        telefone = st.text_input(
            "Telefone do jovem (WhatsApp)",
            placeholder="(11) 91234-5678",
            key=f"novo_tel_{v}",
            on_change=_on_change_tel, args=(f"novo_tel_{v}",),
            help="Digite os números — a formatação acontece sozinha.",
        )

    st.markdown("##### 👨‍👩‍👧 Responsável")
    st.caption("⚠️ Obrigatório para todos os jovens, independente da idade.")
    col3, col4 = st.columns(2)
    with col3:
        nome_resp = st.text_input("Nome do responsável *", key=f"novo_nome_resp_{v}")
    with col4:
        tel_resp = st.text_input(
            "Telefone do responsável *",
            placeholder="(11) 91234-5678",
            key=f"novo_tel_resp_{v}",
            on_change=_on_change_tel, args=(f"novo_tel_resp_{v}",),
        )

    st.markdown("##### 📝 Observações (opcional)")
    obs = st.text_area(
        "Anotações sobre o jovem",
        placeholder="Ex: Está passando por um momento difícil, gosta de música, toca violão...",
        key=f"novo_obs_{v}",
        height=100,
    )

    st.write("")
    col_salvar, _ = st.columns([1, 3])
    with col_salvar:
        if st.button("💾 Salvar contato", type="primary", use_container_width=True):
            ok, msg = criar_contato(
                nome=nome,
                nascimento=nascimento.isoformat() if nascimento else None,
                telefone=telefone,
                nome_responsavel=nome_resp,
                telefone_responsavel=tel_resp,
                observacoes=obs,
            )
            if ok:
                st.session_state["novo_contato_versao"] += 1
                st.session_state["contatos_sub"] = "lista"
                flash(msg, "success")
                st.rerun()
            else:
                st.error(msg)


def pagina_detalhe_contato(contato_id):
    contato = obter_contato(contato_id)
    if not contato:
        st.error("Contato não encontrado.")
        if st.button("← Voltar"):
            st.session_state["contatos_sub"] = "lista"
            st.rerun()
        return

    if st.button("← Voltar para contatos"):
        st.session_state["contatos_sub"] = "lista"
        st.rerun()

    idade = calcular_idade(contato.get("data_nascimento"))
    idade_txt = f" · {idade} anos" if idade is not None else ""

    st.markdown(f"## 👤 {contato['nome']}{idade_txt}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### 📋 Informações do jovem")
        st.markdown(f"**Nome:** {contato['nome']}")
        st.markdown(f"**Nascimento:** {fmt_data(contato['data_nascimento'])}")
        st.markdown(f"**Telefone:** {contato['telefone'] or '—'}")
        if contato.get("criado_em"):
            st.caption(f"Cadastrado em {fmt_data(contato['criado_em'][:10])}")

        url_j = link_whatsapp(contato.get("telefone"), contato["nome"])
        if url_j:
            st.link_button("💬 Chamar jovem no WhatsApp", url_j, use_container_width=True)

    with col2:
        st.markdown("##### 👨‍👩‍👧 Responsável")
        resp_nome = contato.get("nome_responsavel") or ""
        resp_tel = contato.get("telefone_responsavel") or ""
        if resp_nome or resp_tel:
            st.markdown(f"**Nome:** {resp_nome or '—'}")
            st.markdown(f"**Telefone:** {resp_tel or '—'}")
            url_r = link_whatsapp(resp_tel, resp_nome or "responsável",
                                  template=f"Olá {resp_nome.split()[0] if resp_nome else ''}! "
                                           f"Falo do grupo O.P.A sobre {contato['nome']}. Tudo bem?")
            if url_r:
                st.link_button("💬 Chamar responsável no WhatsApp", url_r, use_container_width=True)
        else:
            st.caption("Nenhum responsável cadastrado.")

    obs_txt = (contato.get("observacoes") or "").strip()
    if obs_txt:
        st.markdown("##### 📝 Observações")
        with st.container(border=True):
            st.write(obs_txt)

    col_edit, col_del = st.columns([1, 1])
    with col_edit:
        with st.expander("✏️ Editar informações"):
            v = contato_id
            novo_nome = st.text_input("Nome", value=contato["nome"], key=f"edit_nome_{v}")
            existente_edit = nome_ja_existe(novo_nome, ignorar_id=contato_id) if novo_nome.strip() else None
            if existente_edit:
                st.warning(f"⚠️ Já existe outro jovem com esse nome: **{existente_edit}**.")

            col_a, col_b = st.columns(2)
            with col_a:
                nasc_str = contato.get("data_nascimento") or "2005-01-01"
                try:
                    nasc_val = datetime.strptime(nasc_str, "%Y-%m-%d").date()
                except Exception:
                    nasc_val = date(2005, 1, 1)
                novo_nasc = st.date_input(
                    "Nascimento", value=nasc_val,
                    min_value=date(1940, 1, 1), max_value=date.today(),
                    format="DD/MM/YYYY", key=f"edit_nasc_{v}",
                )
            with col_b:
                novo_tel = st.text_input(
                    "Telefone", value=contato.get("telefone") or "",
                    key=f"edit_tel_{v}",
                    on_change=_on_change_tel, args=(f"edit_tel_{v}",),
                )
            col_c, col_d = st.columns(2)
            with col_c:
                novo_nome_resp = st.text_input(
                    "Nome do responsável", value=contato.get("nome_responsavel") or "",
                    key=f"edit_nome_resp_{v}",
                )
            with col_d:
                novo_tel_resp = st.text_input(
                    "Telefone do responsável", value=contato.get("telefone_responsavel") or "",
                    key=f"edit_tel_resp_{v}",
                    on_change=_on_change_tel, args=(f"edit_tel_resp_{v}",),
                )
            novas_obs = st.text_area(
                "Observações", value=contato.get("observacoes") or "",
                key=f"edit_obs_{v}", height=100,
            )

            if st.button("💾 Salvar alterações", type="primary", key=f"salvar_edit_{v}"):
                if not novo_nome_resp.strip():
                    st.error("O nome do responsável é obrigatório.")
                elif not limpar_telefone(novo_tel_resp):
                    st.error("O telefone do responsável é obrigatório.")
                else:
                    ok, msg = atualizar_contato(
                        contato_id, novo_nome, novo_nasc.isoformat(),
                        novo_tel, novo_nome_resp, novo_tel_resp, novas_obs,
                    )
                    if ok:
                        flash("Contato atualizado.", "success")
                        st.rerun()
                    else:
                        st.error(msg)

    with col_del:
        with st.expander("🗑️ Remover contato"):
            st.warning("Isso apaga todas as presenças registradas deste jovem.")
            if st.button("Remover definitivamente", type="secondary", key=f"del_contato_{contato_id}"):
                nome_removido = contato["nome"]
                deletar_contato(contato_id)
                st.session_state["contatos_sub"] = "lista"
                flash(f"{nome_removido} removido.", "success")
                st.rerun()

    st.divider()
    st.markdown("## 📈 Frequência")
    _render_frequencia_contato(contato_id)


def _render_frequencia_contato(contato_id):
    hoje = date.today()
    anos = list(range(hoje.year, hoje.year - 5, -1))

    col_ano, _ = st.columns([1, 3])
    with col_ano:
        ano = st.selectbox("📆 Ano", options=anos, index=0, key=f"freq_ano_{contato_id}")

    ini, fim = f"{ano}-01-01", f"{ano}-12-31"

    with conn_db() as conn:
        total_grupos = conn.execute(
            "SELECT COUNT(DISTINCT data) FROM presencas WHERE presente = 1 AND data >= ? AND data <= ?",
            (ini, fim),
        ).fetchone()[0]
        presentes_jovem = conn.execute(
            "SELECT COUNT(*) FROM presencas WHERE contato_id = ? AND presente = 1 AND data >= ? AND data <= ?",
            (contato_id, ini, fim),
        ).fetchone()[0]
        ultima = conn.execute(
            "SELECT MAX(data) FROM presencas WHERE contato_id = ? AND presente = 1",
            (contato_id,),
        ).fetchone()[0]
        total_presencas_geral = conn.execute(
            "SELECT COUNT(*) FROM presencas WHERE contato_id = ? AND presente = 1",
            (contato_id,),
        ).fetchone()[0]
        df_mes = pd.read_sql_query(
            """SELECT substr(data, 6, 2) AS mes, COUNT(*) AS presencas
               FROM presencas WHERE contato_id = ? AND presente = 1 AND data >= ? AND data <= ?
               GROUP BY mes""",
            conn, params=(contato_id, ini, fim),
        )

    pct = (presentes_jovem / total_grupos * 100) if total_grupos > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Grupos em {ano}", total_grupos)
    m2.metric("Presenças no ano", presentes_jovem)
    m3.metric("Frequência", f"{pct:.1f}%")
    m4.metric("Presenças totais", total_presencas_geral)

    if ultima:
        dias = (hoje - datetime.strptime(ultima, "%Y-%m-%d").date()).days
        if dias == 0:
            st.success(f"🎉 Veio **hoje** ({fmt_data(ultima)})!")
        else:
            st.caption(f"🕐 Última vez que veio: **{fmt_data(ultima)}** ({fmt_dias(dias)} atrás)")
    else:
        st.warning("⚠️ Este jovem **nunca veio** em nenhum grupo.")
        return

    if total_grupos > 0:
        st.markdown(f"##### Presenças mês a mês — {ano}")
        meses = [f"{m:02d}" for m in range(1, 13)]
        nomes_meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                       "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
        df_mes = df_mes.set_index("mes").reindex(meses, fill_value=0).reset_index()
        df_mes.columns = ["mes", "presencas"]
        df_mes["nome_mes"] = df_mes["mes"].apply(lambda x: nomes_meses[int(x) - 1])

        chart = (
            alt.Chart(df_mes)
            .mark_bar(color="#7c5cff", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("nome_mes:N", sort=nomes_meses, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("presencas:Q", title="Presenças"),
                tooltip=[alt.Tooltip("nome_mes:N", title="Mês"),
                         alt.Tooltip("presencas:Q", title="Presenças")],
            ).properties(height=200)
        )
        st.altair_chart(chart, use_container_width=True)


# =========================================================
# PÁGINA: ANIVERSARIANTES (com botão WhatsApp)
# =========================================================
def pagina_aniversariantes():
    st.header("🎂 Aniversariantes do mês")
    st.caption("Mande um parabéns! Clique em 💬 pra abrir o WhatsApp com mensagem pronta.")

    hoje = date.today()
    df = aniversariantes_do_mes(hoje.month)

    if df.empty:
        st.info(f"Ninguém faz aniversário em {hoje.strftime('%B')}.")
        return

    st.caption(f"**{len(df)}** aniversariante(s) em {hoje.strftime('%m/%Y')}")

    for _, row in df.iterrows():
        dia = row["dia"]
        eh_hoje = (dia == hoje.day)

        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 1])

            with c1:
                if eh_hoje:
                    st.markdown(f"### 🎉 **{row['nome']}** — **HOJE!**")
                else:
                    st.markdown(f"### {row['nome']}")

            with c2:
                st.markdown(f"📅 **{dia:02d}/{hoje.month:02d}**")
                st.caption(f"📱 {row['telefone'] or '—'}")

            with c3:
                primeiro_nome = row["nome"].split()[0] if row["nome"] else ""
                template = (
                    f"🎉 Feliz aniversário, {primeiro_nome}! "
                    f"Que Deus te abençoe muito e te dê um ano cheio de coisas boas! "
                    f"Todo o grupo O.P.A te deseja parabéns! 💛🙌"
                )
                url = link_whatsapp(row["telefone"], row["nome"], template=template)
                if url:
                    st.link_button("💬 Parabéns", url, use_container_width=True, type="primary")
                else:
                    st.button("Sem telefone", disabled=True, use_container_width=True,
                              key=f"sem_tel_aniv_{row['id']}")

    st.write("")
    st.divider()
    st.caption("💡 **Dica:** os aniversariantes de hoje aparecem com **HOJE!** em destaque. "
               "Manda o parabéns rapidinho — eles adoram! 🎉")


# =========================================================
# APP
# =========================================================
init_db()

if not st.session_state.get("auth"):
    tela_login()
    st.stop()

if "_flash" in st.session_state:
    msg, tipo = st.session_state.pop("_flash")
    (st.success if tipo == "success" else st.error)(msg)

if "pagina" not in st.session_state:
    st.session_state["pagina"] = "🏠 Início"

nav = st.session_state.pop("_ir_para", None)
if nav:
    st.session_state["pagina"] = nav["pagina"]

OPCOES = [
    "🏠 Início",
    "📋 Lista de Presença",
    "📅 Agenda",
    "📈 Frequência",
    "👥 Contatos",
    "🎂 Aniversariantes",
]

with st.sidebar:
    st.markdown("### 🙌 O.P.A")
    st.caption("Painel da Coordenação")
    try:
        idx = OPCOES.index(st.session_state["pagina"])
    except ValueError:
        idx = 0
    escolha = st.radio(
        "Ir para", OPCOES, index=idx, key="radio_nav",
        label_visibility="collapsed",
    )
    if escolha != st.session_state["pagina"]:
        st.session_state["pagina"] = escolha
        st.session_state.pop("contato_atual_id", None)
        st.session_state.pop("presenca_evento_data", None)
        st.session_state["contatos_sub"] = "lista"
        st.session_state["presenca_sub"] = "selecao"
        st.rerun()

    st.divider()
    if st.button("Sair"):
        st.session_state["auth"] = False
        st.rerun()

menu = st.session_state["pagina"]

if menu == "🏠 Início":
    pagina_home()
elif menu == "📋 Lista de Presença":
    pagina_presenca()
elif menu == "📅 Agenda":
    pagina_agenda()
elif menu == "📈 Frequência":
    pagina_frequencia()
elif menu == "👥 Contatos":
    pagina_contatos()
elif menu == "🎂 Aniversariantes":
    pagina_aniversariantes()