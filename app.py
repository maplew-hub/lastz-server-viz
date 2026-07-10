import os
import sqlite3
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="Last Z — Server Intel", layout="wide", page_icon="⚔️")

LOCAL_DB = os.path.expanduser("~/lastz-tools/data/players.db")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def fmt_power(val):
    if pd.isna(val):
        return "—"
    val = int(val)
    if val >= 1_000_000_000_000:
        return f"{val / 1_000_000_000_000:.2f}T"
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.2f}B"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    return f"{val:,}"

def render_tape(rows, label_a, label_b, header_a=None, header_b=None):
    """Build a boxing-style tale of the tape HTML table.
    rows: list of (label, raw_a, raw_b, fmt_a, fmt_b)
    header_a/header_b: full header text override (default "Server {label}")
    """
    header_a = header_a or f"Server {label_a}"
    header_b = header_b or f"Server {label_b}"
    html = f"""<table style="width:100%;border-collapse:collapse;margin:8px 0;">
    <thead><tr>
        <th style="text-align:right;color:#e63946;padding:10px 20px;font-size:1.15em;width:35%;">
            {header_a}</th>
        <th style="text-align:center;color:#555;width:30%;font-weight:normal;font-size:0.85em;
            border-left:1px solid #333;border-right:1px solid #333;">STAT</th>
        <th style="text-align:left;color:#4ecdc4;padding:10px 20px;font-size:1.15em;width:35%;">
            {header_b}</th>
    </tr></thead><tbody>"""

    for label, raw_a, raw_b, fmt_a, fmt_b in rows:
        try:
            a_num = float(raw_a)
            b_num = float(raw_b)
            if a_num == b_num:
                a_wins = None
            else:
                a_wins = a_num > b_num
            delta = fmt_power(abs(a_num - b_num)) if a_wins is not None else ""
        except Exception:
            a_wins = None
            delta = ""

        delta_span = f" <span style='font-size:0.8em;color:#4caf50;'>(+{delta})</span>"
        delta_a = delta_span if a_wins is True  else ""
        delta_b = delta_span if a_wins is False else ""

        if a_wins is True:
            sa = "font-weight:bold;font-size:1.05em;color:#e63946;"
            sb = "color:#555;"
        elif a_wins is False:
            sa = "color:#555;"
            sb = "font-weight:bold;font-size:1.05em;color:#4ecdc4;"
        else:
            sa = sb = "color:#aaa;"

        html += f"""<tr style="border-top:1px solid #2a2a2a;">
            <td style="text-align:right;padding:10px 20px;{sa}">{fmt_a}{delta_a}</td>
            <td style="text-align:center;padding:8px;color:#888;font-size:0.85em;
                border-left:1px solid #333;border-right:1px solid #333;">{label}</td>
            <td style="text-align:left;padding:10px 20px;{sb}">{fmt_b}{delta_b}</td>
        </tr>"""

    html += "</tbody></table>"
    return html

@st.cache_data(ttl=60)
def load_data_local():
    con = sqlite3.connect(LOCAL_DB)
    players_df = pd.read_sql_query("""
        SELECT name AS Name, alliance_abbr AS Tag, alliance_name AS Alliance,
               hq_level AS HQ, server AS Server,
               original_server AS [Orig Server], s3_server AS [S3 Server],
               power AS Power,
               migrate_power AS [Migrate Power], hero_power AS [Hero Power],
               building_power AS Building, science_power AS Science,
               army_power AS Troop, tank_power AS Tank,
               player_max_power AS [Max Power], last_seen AS [Last Seen]
        FROM players ORDER BY power DESC
    """, con)
    alliances_df = pd.read_sql_query("""
        SELECT a.name AS Alliance, a.abbr AS Tag, a.server AS Server,
               a.rank AS Rank, a.fightpower AS [Fight Power],
               a.cur_member AS Members, a.max_member AS [Max Members],
               COUNT(p.uid) AS [Players in DB],
               SUM(CASE WHEN p.migrate_power IS NOT NULL THEN 1 ELSE 0 END) AS [With Migrate],
               a.last_seen AS [Last Seen]
        FROM alliances a
        LEFT JOIN players p ON p.alliance_id = a.alliance_id
        GROUP BY a.alliance_id
        ORDER BY a.server ASC, a.rank ASC
    """, con)
    con.close()
    return players_df, alliances_df

@st.cache_data(ttl=300)
def load_data_sheets():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(st.secrets["sheet_id"])
    players_df = pd.DataFrame(sh.worksheet("All Players").get_all_records())
    alliances_df = pd.DataFrame(sh.worksheet("Alliances").get_all_records())
    return players_df, alliances_df

def load_data():
    if os.path.exists(LOCAL_DB):
        return load_data_local()
    return load_data_sheets()

def _coerce_numeric(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

CAT_ORDER = ["Elite", "Advanced", "Medium", "Regular", "Unscanned"]

def categorize(migrate_power_series):
    """Elite >160M, Advanced >80M, Medium >40M, Regular (scanned, <=40M), Unscanned (no migrate power)."""
    mp = pd.to_numeric(migrate_power_series, errors="coerce")
    cat = pd.Series("Unscanned", index=migrate_power_series.index)
    cat[mp.notna()]      = "Regular"
    cat[mp > 40_000_000]  = "Medium"
    cat[mp > 80_000_000]  = "Advanced"
    cat[mp > 160_000_000] = "Elite"
    return cat

def migration_overview_rows(df, from_col, to_col):
    """Stayed-vs-Moved comparison rows for render_tape, plus the two slices themselves."""
    d = df.dropna(subset=[from_col, to_col]).copy()
    stayed = d[d[from_col] == d[to_col]]
    moved  = d[d[from_col] != d[to_col]]

    rows = [
        ("Players", len(stayed), len(moved), f"{len(stayed):,}", f"{len(moved):,}"),
        ("Total Power", stayed["Power"].sum(), moved["Power"].sum(),
         fmt_power(stayed["Power"].sum()), fmt_power(moved["Power"].sum())),
        ("Total Migrate Power", stayed["Migrate Power"].sum(), moved["Migrate Power"].sum(),
         fmt_power(stayed["Migrate Power"].sum()), fmt_power(moved["Migrate Power"].sum())),
    ]
    return rows, stayed, moved

def category_summary(df):
    """Category, Count, Total Power table for a slice of players_df."""
    d = df.copy()
    d["Category"] = categorize(d["Migrate Power"])
    summary = (
        d.groupby("Category")
        .agg(Count=("Name", "size"), _total_power=("Power", "sum"))
        .reindex(CAT_ORDER)
        .fillna(0)
    )
    summary["Count"] = summary["Count"].astype(int)
    summary["Total Power"] = summary["_total_power"].apply(fmt_power)
    return summary[["Count", "Total Power"]].reset_index()

def prepare(players_df, alliances_df):
    _coerce_numeric(players_df, ["Power", "Max Power", "Migrate Power", "Hero Power",
                                  "Building", "Science", "Troop", "Tank", "HQ", "Server",
                                  "Orig Server", "S3 Server"])
    _coerce_numeric(alliances_df, ["Fight Power", "Server", "Rank", "Members",
                                    "Max Members", "Players in DB", "With Migrate"])
    return players_df, alliances_df


# ── Header ────────────────────────────────────────────────────────────────────

st.title("⚔️ Last Z — Server Intel")

try:
    players_df, alliances_df = load_data()
    players_df, alliances_df = prepare(players_df, alliances_df)
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# ── Sidebar filters ───────────────────────────────────────────────────────────

st.sidebar.header("Filters")

all_servers = sorted(players_df["Server"].dropna().astype(float).astype(int).unique().tolist())
DEFAULT_SERVERS = [231, 235, 241, 249]
selected_servers = st.sidebar.multiselect(
    "Servers to compare", all_servers,
    default=[s for s in DEFAULT_SERVERS if s in all_servers]
)

top_n = st.sidebar.select_slider(
    "Top N players per server",
    options=[3, 5, 10, 25, 50, 100, 150, 200, 300, 500],
    value=100
)

power_metric = st.sidebar.selectbox(
    "Player power metric",
    ["Power", "Max Power", "Migrate Power"],
    index=0
)

if not selected_servers:
    st.warning("Select at least one server in the sidebar.")
    st.stop()

# ── Filtered data ─────────────────────────────────────────────────────────────

filtered_players = players_df[players_df["Server"].isin(selected_servers)].copy()
filtered_alliances = alliances_df[alliances_df["Server"].isin(selected_servers)].copy()

top_players = (
    filtered_players
    .dropna(subset=[power_metric])
    .sort_values(power_metric, ascending=False)
    .groupby("Server", group_keys=False)
    .head(top_n)
)

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab5, tab1, tab2, tab3, tab4, tab6 = st.tabs([
    "⚖️ Tale of the Tape", "📊 Server Totals", "📦 Power Distribution", "🏰 Alliances", "🔍 Player Table",
    "🔄 Migration Turnover"
])


# ── Shared: server order by total power (used by tab1 + tab2) ────────────────

server_totals = (
    top_players.groupby("Server")[power_metric]
    .sum()
    .reset_index()
    .sort_values(power_metric, ascending=False)
)
player_counts = top_players.groupby("Server").size().reset_index(name="Players")
server_totals = server_totals.merge(player_counts, on="Server")
server_totals["Server"] = server_totals["Server"].astype(float).astype(int).astype(str)
server_totals["Formatted"] = server_totals[power_metric].apply(fmt_power) + "<br>" + server_totals["Players"].astype(str) + " players"
server_order = server_totals["Server"].tolist()
player_map = dict(zip(server_totals["Server"], server_totals["Players"]))

# ── Tab 1: Server Totals ──────────────────────────────────────────────────────

with tab1:
    st.subheader(f"Combined {power_metric} — Top {top_n} Players per Server")
    fig = px.bar(
        server_totals, x="Server", y=power_metric,
        text="Formatted",
        color="Server",
        color_discrete_sequence=px.colors.qualitative.Bold,
        category_orders={"Server": server_order},
    )
    fig.update_traces(textposition="outside")
    fig.for_each_trace(lambda t: t.update(
        textfont=dict(color="red" if player_map.get(t.name, top_n) < top_n else "white")
    ))
    fig.update_layout(
        showlegend=False,
        yaxis_title=power_metric,
        xaxis_title="Server",
        xaxis_type="category",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    event = st.plotly_chart(fig, width='stretch', on_select="rerun", key="totals_chart")

    # Summary metrics
    cols = st.columns(min(len(server_totals), 6))
    for i in range(min(len(server_totals), len(cols))):
        row = server_totals.iloc[i]
        cols[i].metric(f"Server {int(float(row['Server']))}", fmt_power(row[power_metric]))

    with st.expander("Show data table"):
        tbl = server_totals[["Server", "Players"]].copy()
        st.dataframe(tbl, width='stretch', hide_index=True)

    # ── Click-to-drill player table ───────────────────────────────────────────
    selected_points = (event.selection.points if event and event.selection else [])
    clicked_server = selected_points[0]["x"] if selected_points else None

    if clicked_server:
        st.subheader(f"Top {top_n} Players — Server {clicked_server}")
        drill = (
            top_players[top_players["Server"].astype(float).astype(int).astype(str) == clicked_server]
            .sort_values("Max Power", ascending=False)
            .copy()
        )
        fmt_cols = ["Power", "Max Power", "Science", "Tank", "Hero Power"]
        for col in fmt_cols:
            if col in drill.columns:
                drill[col] = pd.to_numeric(drill[col], errors="coerce").apply(fmt_power)
        display_cols = ["Name", "Tag", "Alliance", "HQ", "Power", "Max Power",
                        "Science", "Tank", "Hero Power", "Last Seen"]
        display_cols = [c for c in display_cols if c in drill.columns]
        drill = drill[display_cols].rename(columns={"Science": "Tech"})
        st.dataframe(drill, width='stretch', hide_index=True)
    else:
        st.caption("Click a bar to see that server's players.")


# ── Tab 2: Power Distribution ─────────────────────────────────────────────────

with tab2:
    st.subheader(f"{power_metric} Distribution — Top {top_n} per Server")

    box_df = top_players.copy()
    box_df["Server"] = box_df["Server"].astype(float).astype(int).astype(str)

    fig2 = px.box(
        box_df, x="Server", y=power_metric,
        color="Server",
        color_discrete_sequence=px.colors.qualitative.Bold,
        points="outliers",
        category_orders={"Server": server_order},
    )
    fig2.update_layout(
        showlegend=False,
        xaxis_type="category",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig2, width='stretch')

    # Per-server stats table ordered strongest to weakest
    stats = (
        top_players.groupby("Server")[power_metric]
        .agg(["max", "median", "mean", "min", "count"])
        .reset_index()
    )
    stats.columns = ["Server", "Top Player", "Median", "Average", "Lowest (in top N)", "Players"]
    stats["Server"] = stats["Server"].astype(float).astype(int).astype(str)
    stats = stats.set_index("Server").loc[server_order].reset_index()
    for col in ["Top Player", "Median", "Average", "Lowest (in top N)"]:
        stats[col] = stats[col].apply(fmt_power)

    def highlight_low(row):
        color = "color: red" if row["Players"] < top_n else ""
        return [""] * (len(row) - 1) + [color]

    st.dataframe(stats.style.apply(highlight_low, axis=1), width='stretch', hide_index=True)


# ── Tab 3: Alliances ──────────────────────────────────────────────────────────

with tab3:
    st.subheader("Alliance Fight Power by Server")

    al_df = filtered_alliances.dropna(subset=["Fight Power"]).copy()
    al_df["Server"] = al_df["Server"].astype(float).astype(int).astype(str)

    top_n_al = st.slider("Show top N alliances per server", 3, 20, 10)
    al_top = (
        al_df.sort_values("Fight Power", ascending=False)
        .groupby("Server", group_keys=False)
        .head(top_n_al)
    )
    al_top["FP Formatted"] = al_top["Fight Power"].apply(fmt_power)

    fig3 = px.bar(
        al_top.sort_values(["Server", "Fight Power"], ascending=[True, False]),
        x="Tag", y="Fight Power",
        color="Server",
        text="FP Formatted",
        hover_data=["Alliance", "Rank", "Members", "Players in DB", "With Migrate"],
        color_discrete_sequence=px.colors.qualitative.Bold,
        barmode="group",
    )
    fig3.update_traces(textposition="outside")
    fig3.update_layout(
        xaxis_title="Alliance Tag",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig3, width='stretch')

    # Server-level alliance totals (sum of top N alliances)
    server_al_totals = (
        al_top.groupby("Server")["Fight Power"]
        .sum()
        .reset_index()
        .sort_values("Fight Power", ascending=False)
    )
    server_al_totals["Fight Power"] = server_al_totals["Fight Power"].apply(fmt_power)
    st.caption(f"Combined fight power — top {top_n_al} alliances per server")
    st.dataframe(server_al_totals, width='stretch', hide_index=True)

    with st.expander("Full alliance table"):
        display_al = filtered_alliances.copy()
        display_al["Fight Power"] = display_al["Fight Power"].apply(fmt_power)
        st.dataframe(
            display_al[["Server", "Rank", "Alliance", "Tag", "Fight Power",
                         "Members", "Max Members", "Players in DB", "With Migrate"]]
            .sort_values(["Server", "Rank"]),
            width='stretch', hide_index=True
        )


# ── Tab 4: Player Table ───────────────────────────────────────────────────────

with tab4:
    st.subheader("Player Search")

    col1, col2, col3 = st.columns(3)
    name_filter = col1.text_input("Search name")
    al_filter = col2.text_input("Search alliance tag")
    show_top_only = col3.checkbox(f"Top {top_n} per server only", value=True)

    tbl = top_players if show_top_only else filtered_players
    tbl = tbl.copy()

    if name_filter:
        tbl = tbl[tbl["Name"].str.contains(name_filter, case=False, na=False)]
    if al_filter:
        tbl = tbl[tbl["Tag"].str.contains(al_filter, case=False, na=False)]

    for col in ["Power", "Max Power", "Migrate Power", "Hero Power", "Building", "Science", "Troop", "Tank"]:
        if col in tbl.columns:
            tbl[col] = tbl[col].apply(fmt_power)

    display_cols = ["Server", "Name", "Tag", "Alliance", "HQ",
                    "Max Power", "Power", "Migrate Power", "Last Seen"]
    display_cols = [c for c in display_cols if c in tbl.columns]

    st.dataframe(
        tbl[display_cols].sort_values(["Server", "Max Power"], ascending=[True, False]),
        width='stretch', hide_index=True
    )
    st.caption(f"{len(tbl):,} players shown")


# ── Tab 5: Tale of the Tape ───────────────────────────────────────────────────

with tab5:
    st.subheader("⚖️ Tale of the Tape")

    tape_defaults = [s for s in DEFAULT_SERVERS if s in all_servers]
    default_a = 241 if 241 in all_servers else (tape_defaults[0] if tape_defaults else all_servers[0])
    default_b = 249 if 249 in all_servers else (tape_defaults[1] if len(tape_defaults) > 1 else (all_servers[1] if len(all_servers) > 1 else all_servers[0]))

    col_l, col_mid, col_r = st.columns([5, 1, 5])
    server_a = col_l.selectbox("Left Server", all_servers,
                                index=all_servers.index(default_a),
                                key="tape_a")
    col_mid.markdown("<div style='text-align:center;padding-top:32px;color:#555;font-size:1.4em;'>VS</div>",
                     unsafe_allow_html=True)
    server_b = col_r.selectbox("Right Server", all_servers,
                                index=all_servers.index(default_b),
                                key="tape_b")

    if server_a == server_b:
        st.warning("Select two different servers to compare.")
        st.stop()

    pa = players_df[players_df["Server"] == server_a].copy()
    pb = players_df[players_df["Server"] == server_b].copy()
    aa = alliances_df[alliances_df["Server"] == server_a].copy()
    ab = alliances_df[alliances_df["Server"] == server_b].copy()

    # ── Section 1: Server Summary ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Server Summary")

    top10_al_a = aa.dropna(subset=["Rank"]).sort_values("Rank").head(10)
    top10_al_b = ab.dropna(subset=["Rank"]).sort_values("Rank").head(10)
    top10_tags_a = set(top10_al_a["Tag"].dropna())
    top10_tags_b = set(top10_al_b["Tag"].dropna())

    accts_top10_a = len(pa[pa["Tag"].isin(top10_tags_a)])
    accts_top10_b = len(pb[pb["Tag"].isin(top10_tags_b)])
    fp_top10_a  = top10_al_a["Fight Power"].sum()
    fp_top10_b  = top10_al_b["Fight Power"].sum()
    hero_a = len(pa[pd.to_numeric(pa["Hero Power"], errors="coerce") >= 100_000_000])
    hero_b = len(pb[pd.to_numeric(pb["Hero Power"], errors="coerce") >= 100_000_000])
    tech_a = len(pa[pd.to_numeric(pa["Science"],    errors="coerce") >= 25_000_000])
    tech_b = len(pb[pd.to_numeric(pb["Science"],    errors="coerce") >= 25_000_000])
    tank_a = len(pa[pd.to_numeric(pa["Tank"],       errors="coerce") >= 15_000_000])
    tank_b = len(pb[pd.to_numeric(pb["Tank"],       errors="coerce") >= 15_000_000])
    scanned_a = int(pa[pd.to_numeric(pa["Science"], errors="coerce").notna() |
                       pd.to_numeric(pa["Tank"],    errors="coerce").notna()].shape[0])
    scanned_b = int(pb[pd.to_numeric(pb["Science"], errors="coerce").notna() |
                       pd.to_numeric(pb["Tank"],    errors="coerce").notna()].shape[0])

    summary_rows = [
        ("Players Scanned",                      scanned_a,    scanned_b,    str(scanned_a),           str(scanned_b)),
        ("Accounts in Top 10 Alliances",         accts_top10_a, accts_top10_b, str(accts_top10_a),     str(accts_top10_b)),
        ("Total Fight Power (Top 10 Alliances)", fp_top10_a,    fp_top10_b,    fmt_power(fp_top10_a),  fmt_power(fp_top10_b)),
        ("Accounts with 100M+ Hero Power",       hero_a,        hero_b,        str(hero_a),            str(hero_b)),
        ("Accounts with 25M+ Tech Power",        tech_a,        tech_b,        str(tech_a),            str(tech_b)),
        ("Accounts with 15M+ Tank Power",        tank_a,        tank_b,        str(tank_a),            str(tank_b)),
    ]
    st.markdown(render_tape(summary_rows, server_a, server_b), unsafe_allow_html=True)

    # ── Section 2: Max Power Breakdown ───────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Max Power Breakdown")

    mp_a = pd.to_numeric(pa["Max Power"], errors="coerce")
    mp_b = pd.to_numeric(pb["Max Power"], errors="coerce")

    mp_rows = []
    for threshold in [700_000_000, 600_000_000, 500_000_000, 400_000_000, 300_000_000, 200_000_000]:
        cnt_a = int((mp_a >= threshold).sum())
        cnt_b = int((mp_b >= threshold).sum())
        label = f"Over {fmt_power(threshold)}"
        mp_rows.append((label, cnt_a, cnt_b, str(cnt_a), str(cnt_b)))

    st.markdown(render_tape(mp_rows, server_a, server_b), unsafe_allow_html=True)

    # ── Section 3: Power Breakdown by Tier ───────────────────────────────────
    st.markdown("---")
    st.markdown("#### Power Breakdown by Tier")

    TIERS = [3, 5, 10, 20, 50, 100]
    POWER_COLS = [
        ("Power",      "Power"),
        ("Max Power",  "Max Power"),
        ("Tech Power", "Science"),
        ("Tank Power", "Tank"),
        ("Hero Power", "Hero Power"),
    ]

    power_subtabs = st.tabs([label for label, _ in POWER_COLS])

    for subtab, (_, col) in zip(power_subtabs, POWER_COLS):
        with subtab:
            sorted_a = pa.dropna(subset=[col]).sort_values(col, ascending=False)
            sorted_b = pb.dropna(subset=[col]).sort_values(col, ascending=False)

            tier_rows = []
            for n in TIERS:
                sum_a = sorted_a.head(n)[col].sum()
                sum_b = sorted_b.head(n)[col].sum()
                tier_rows.append((f"Top {n}", sum_a, sum_b, fmt_power(sum_a), fmt_power(sum_b)))

            st.markdown(render_tape(tier_rows, server_a, server_b), unsafe_allow_html=True)


# ── Tab 6: Migration Turnover ─────────────────────────────────────────────────

with tab6:
    st.subheader("🔄 Migration Turnover")
    st.caption("Uses current Power/Migrate Power snapshots — there's no historical power-at-migration-time data, "
               "so this compares who moved vs. stayed using today's numbers.")

    have_mig_cols = "Orig Server" in players_df.columns and "S3 Server" in players_df.columns
    if not have_mig_cols:
        st.warning("Orig Server / S3 Server columns not found in the data source yet.")
        st.stop()

    # ── High-level overview: both migrations, all servers ─────────────────────
    st.markdown("### Overview — All Servers")

    for mig_label, from_col, to_col in [
        ("Migration 1  (Original → S3)", "Orig Server", "S3 Server"),
        ("Migration 2  (S3 → Current)",  "S3 Server",   "Server"),
    ]:
        st.markdown(f"#### {mig_label}")
        rows, stayed, moved = migration_overview_rows(players_df, from_col, to_col)
        st.markdown(render_tape(rows, "stayed", "moved",
                                 header_a="Stayed", header_b="Moved"),
                    unsafe_allow_html=True)

        cat_col_l, cat_col_r = st.columns(2)
        cat_col_l.caption("Stayed — by category")
        cat_col_l.dataframe(category_summary(stayed), hide_index=True, width='stretch')
        cat_col_r.caption("Moved — by category")
        cat_col_r.dataframe(category_summary(moved), hide_index=True, width='stretch')
        st.markdown("---")

    # ── Per-server turnover detail ──────────────────────────────────────────────
    st.markdown("### Per-Server Detail")

    mig_choice = st.selectbox(
        "Migration stage",
        ["Migration 1 (Original → S3)", "Migration 2 (S3 → Current)"],
        key="turnover_mig_choice",
    )
    if mig_choice.startswith("Migration 1"):
        from_col, to_col = "Orig Server", "S3 Server"
    else:
        from_col, to_col = "S3 Server", "Server"

    mig_df = players_df.dropna(subset=[from_col, to_col]).copy()
    mig_df[from_col] = mig_df[from_col].astype(int)
    mig_df[to_col]   = mig_df[to_col].astype(int)

    server_options = sorted(set(mig_df[from_col].unique()) | set(mig_df[to_col].unique()))
    if not server_options:
        st.info("No players with both ends of this migration recorded yet.")
        st.stop()
    turnover_server = st.selectbox("Server", server_options, key="turnover_server")

    stayed_box  = mig_df[(mig_df[from_col] == turnover_server) & (mig_df[to_col] == turnover_server)]
    joined_box  = mig_df[(mig_df[to_col] == turnover_server)   & (mig_df[from_col] != turnover_server)]
    left_box    = mig_df[(mig_df[from_col] == turnover_server) & (mig_df[to_col] != turnover_server)]
    current_box = mig_df[mig_df[to_col] == turnover_server]

    boxes = [
        ("✅ Stayed",  stayed_box),
        ("➡️ Joined",  joined_box),
        ("⬅️ Left",    left_box),
        ("🏠 Current (post-migration)", current_box),
    ]

    box_cols = st.columns(4)
    for box_col, (title, box_df) in zip(box_cols, boxes):
        with box_col:
            st.markdown(f"**{title}**")
            st.caption(f"{len(box_df):,} players · {fmt_power(box_df['Power'].sum())} total power")
            st.dataframe(category_summary(box_df), hide_index=True, width='stretch')

    detail_tabs = st.tabs([title for title, _ in boxes])
    for dtab, (title, box_df) in zip(detail_tabs, boxes):
        with dtab:
            disp = box_df.sort_values("Power", ascending=False).copy()
            for c in ["Power", "Migrate Power"]:
                disp[c] = disp[c].apply(fmt_power)
            display_cols = ["Name", "Tag", "Alliance", "HQ", "Power", "Migrate Power",
                            "Orig Server", "S3 Server", "Server"]
            display_cols = [c for c in display_cols if c in disp.columns]
            st.dataframe(disp[display_cols], hide_index=True, width='stretch')
            st.caption(f"{len(disp):,} players shown")
