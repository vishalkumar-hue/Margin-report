"""
Margin Dashboard - Live Streamlit App
Reads live data from a public Google Sheet and renders the same
Overview + Project-wise dashboard as the original HTML version.
"""

import re
import urllib.parse
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ----------------------------------------------------------------------
# CONFIG - apna sheet ID / tab name yaha change kar sakte ho
# ----------------------------------------------------------------------
DEFAULT_SHEET_ID = "1u722Jf7tTX5l195AHxSU_fMHOQdZacoeAlLGqmgdPFc"
DEFAULT_SHEET_NAME = "Raw Data"
CACHE_TTL_SECONDS = 300  # 5 min auto-refresh cache

st.set_page_config(page_title="Margin Dashboard", layout="wide", page_icon="📊")

# ----------------------------------------------------------------------
# DARK THEME CSS (matches original HTML look)
# ----------------------------------------------------------------------
st.markdown("""
<style>
:root{
  --bg:#0b1220; --panel:#121b2e; --panel2:#17233a; --border:#223252;
  --text:#e7ecf5; --muted:#8ea0c2; --gold:#d9a441; --teal:#33b8a8; --coral:#e0665f;
}
.stApp{background:var(--bg); color:var(--text);}
[data-testid="stMetric"]{
  background:linear-gradient(160deg, var(--panel), var(--panel2));
  border:1px solid var(--border); border-radius:10px; padding:14px 16px;
}
[data-testid="stMetricLabel"]{color:var(--muted) !important; font-size:11px !important; text-transform:uppercase;}
h1,h2,h3{color:var(--text);}
.stTabs [data-baseweb="tab"]{color:var(--muted); font-weight:600;}
.stTabs [aria-selected="true"]{color:var(--gold) !important;}
</style>
""", unsafe_allow_html=True)

PALETTE = ['#d9a441','#33b8a8','#e0665f','#8b7ee8','#4f8bd0','#5cc96a','#e0a8d0','#e0d05f','#6fa8e0','#c98b5c']

# ----------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------
def build_csv_url(sheet_id: str, sheet_name: str) -> str:
    encoded_name = urllib.parse.quote(sheet_name)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_name}"


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Google Sheet se data la raha hu...")
def load_raw_data(sheet_id: str, sheet_name: str) -> pd.DataFrame:
    url = build_csv_url(sheet_id, sheet_name)
    df = pd.read_csv(url)
    df.columns = [c.strip() for c in df.columns]
    return df


def clean_numeric(series: pd.Series) -> pd.Series:
    """Strip currency symbols, commas, %, spaces -> float. Blank/junk -> NaN."""
    if series.dtype.kind in "if":
        return series.astype(float)
    cleaned = (
        series.astype(str)
        .str.replace(r"[₹,%\s]", "", regex=True)
        .str.replace(",", "", regex=False)
        .replace({"": None, "nan": None, "None": None, "-": None})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def parse_month(series: pd.Series) -> pd.Series:
    """Month column like 'Apr_26' -> sortable period."""
    return pd.to_datetime(series.astype(str).str.strip(), format="%b_%y", errors="coerce")


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def prepare_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()

    # Map expected columns defensively (in case of small header drift)
    col_map = {
        "ProjectCode": "Project Code Revenue Report",
        "InvoiceStatus": "Invoice Status",
        "ReviewInvoice": "Review Based on Invoice (Raised/Pending)",
        "MarginPct": "Margin Percentage Based On Overall Subtotal",
        "MarginAmt": "Margin Amount Based On Overall Subtotal",
    }
    for new, old in col_map.items():
        if old not in df.columns:
            df[old] = None

    numeric_cols = [
        "Revenue", "Overall Subtotal",
        "Margin Amount Based On Overall Subtotal",
        "Margin Percentage Based On Overall Subtotal",
        "Invoice Qty", "Invoice Rate", "Centre Count", "Total Candidate",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = clean_numeric(df[c])

    # Fallback: compute margin % if missing but revenue+margin amount exist
    mask = df["Margin Percentage Based On Overall Subtotal"].isna() & df["Revenue"].notna() & (df["Revenue"] != 0)
    df.loc[mask, "Margin Percentage Based On Overall Subtotal"] = (
        df.loc[mask, "Margin Amount Based On Overall Subtotal"] / df.loc[mask, "Revenue"] * 100
    )

    df["MonthParsed"] = parse_month(df["Month"])
    df["MonthLabel"] = df["Month"].astype(str).str.strip()

    # Drop fully blank rows (no client and no revenue)
    df = df[~(df["Client"].isna() & df["Revenue"].isna())]

    return df


# ----------------------------------------------------------------------
# FORMATTERS
# ----------------------------------------------------------------------
def fmt_cr(v):
    if pd.isna(v):
        return "-"
    return f"₹{v/1e7:,.2f} Cr"


def fmt_num(v):
    if pd.isna(v):
        return "-"
    return f"{int(round(v)):,}"


# ----------------------------------------------------------------------
# SIDEBAR - source & filters
# ----------------------------------------------------------------------
st.sidebar.header("⚙️ Data Source")
sheet_id = st.sidebar.text_input("Google Sheet ID", value=DEFAULT_SHEET_ID)
sheet_name = st.sidebar.text_input("Tab name", value=DEFAULT_SHEET_NAME)

if st.sidebar.button("🔄 Force refresh now"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(f"Auto-refreshes every {CACHE_TTL_SECONDS // 60} min automatically. Sheet must be shared as 'Anyone with link can view'.")

try:
    raw_df = load_raw_data(sheet_id, sheet_name)
    df = prepare_data(raw_df)
except Exception as e:
    st.error(f"Sheet load nahi ho payi. Check karo sharing settings aur tab name. Error: {e}")
    st.stop()

if df.empty:
    st.warning("Sheet se koi valid data nahi mila. Column headers check karo.")
    st.stop()

st.sidebar.header("🔎 Filters")
months_sorted = (
    df[["MonthLabel", "MonthParsed"]]
    .drop_duplicates()
    .sort_values("MonthParsed")["MonthLabel"]
    .tolist()
)
month_filter = st.sidebar.multiselect("Month", options=months_sorted, default=months_sorted)
service_options = sorted(df["Service"].dropna().unique().tolist())
service_filter = st.sidebar.multiselect("Service", options=service_options, default=service_options)

fdf = df[df["MonthLabel"].isin(month_filter) & df["Service"].isin(service_filter)]

st.sidebar.markdown("---")
st.sidebar.caption(f"Last loaded: {datetime.now().strftime('%d %b %Y, %I:%M %p')}")

# ----------------------------------------------------------------------
# HEADER
# ----------------------------------------------------------------------
st.markdown("## 📊 Margin Dashboard <span style='color:#d9a441'>.</span>", unsafe_allow_html=True)
st.caption("Exam Center Ops · Revenue & Margin Overview (Live from Google Sheets)")

tab1, tab2 = st.tabs(["Overview", "Project-wise"])

# ========================================================================
# TAB 1: OVERVIEW
# ========================================================================
with tab1:
    total_revenue = fdf["Revenue"].sum()
    total_projects = len(fdf)
    avg_margin = fdf["Margin Percentage Based On Overall Subtotal"].mean()
    active_count = (fdf["Project Status"].astype(str).str.strip().str.lower() == "active").sum() if "Project Status" in fdf.columns else None
    pending_col = "Review Based on Invoice (Raised/Pending)" if fdf["Review Based on Invoice (Raised/Pending)"].notna().any() else "Invoice Status"
    pending_count = fdf[pending_col].astype(str).str.strip().str.lower().eq("pending").sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Revenue", fmt_cr(total_revenue))
    k2.metric("Total Projects", fmt_num(total_projects))
    k3.metric("Avg Margin %", f"{avg_margin:.1f}%" if pd.notna(avg_margin) else "-")
    k4.metric("Active Projects", fmt_num(active_count) if active_count is not None else "-")
    k5.metric("Invoices Pending", fmt_num(pending_count))

    st.markdown("---")

    c1, c2 = st.columns([1.3, 1])

    with c1:
        st.markdown("#### Revenue & Margin Trend *(by month)*")
        monthly = (
            fdf.groupby(["MonthLabel", "MonthParsed"], as_index=False)
            .agg(Revenue=("Revenue", "sum"),
                 Margin=("Margin Amount Based On Overall Subtotal", "sum"))
            .sort_values("MonthParsed")
        )
        monthly["MarginPct"] = (monthly["Margin"] / monthly["Revenue"] * 100).where(monthly["Revenue"] != 0)

        fig = go.Figure()
        fig.add_bar(x=monthly["MonthLabel"], y=monthly["Revenue"] / 1e7, name="Revenue (Cr)", marker_color="rgba(79,139,208,0.55)")
        fig.add_bar(x=monthly["MonthLabel"], y=monthly["Margin"] / 1e7, name="Margin (Cr)", marker_color="rgba(217,164,65,0.7)")
        fig.add_trace(go.Scatter(x=monthly["MonthLabel"], y=monthly["MarginPct"], name="Margin %", yaxis="y2",
                                  mode="lines+markers", line=dict(color="#33b8a8", width=3)))
        fig.update_layout(
            barmode="group", template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(title="₹ Cr"), yaxis2=dict(title="Margin %", overlaying="y", side="right"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.3), height=380, margin=dict(t=10)
        )
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("#### Project Status *(by count)*")
        if "Project Status" in fdf.columns:
            status_counts = fdf["Project Status"].value_counts().reset_index()
            status_counts.columns = ["Status", "Count"]
            fig2 = px.pie(status_counts, names="Status", values="Count", hole=0.55,
                          color_discrete_sequence=PALETTE)
            fig2.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", height=380,
                               legend=dict(orientation="h", yanchor="bottom", y=-0.3), margin=dict(t=10))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Project Status column nahi mila.")

    c3, c4 = st.columns(2)

    with c3:
        st.markdown("#### Top Clients by Revenue *(top 10)*")
        client_rev = (
            fdf.groupby("Client", as_index=False)["Revenue"].sum()
            .sort_values("Revenue", ascending=False).head(10)
        )
        fig3 = px.bar(client_rev, x="Revenue", y="Client", orientation="h",
                      color_discrete_sequence=[PALETTE[4]])
        fig3.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           yaxis=dict(autorange="reversed"), height=380, margin=dict(t=10), showlegend=False,
                           xaxis_title="Revenue (₹)")
        st.plotly_chart(fig3, use_container_width=True)

    with c4:
        st.markdown("#### Service-wise Margin % *(all services)*")
        svc = fdf.groupby("Service", as_index=False).agg(
            Revenue=("Revenue", "sum"), Margin=("Margin Amount Based On Overall Subtotal", "sum")
        )
        svc["MarginPct"] = (svc["Margin"] / svc["Revenue"] * 100).where(svc["Revenue"] != 0)
        svc = svc.sort_values("MarginPct", ascending=False)
        fig4 = px.bar(svc, x="Service", y="MarginPct",
                      color=svc["MarginPct"] < 0,
                      color_discrete_map={True: "#e0665f", False: PALETTE[1]})
        fig4.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           height=380, margin=dict(t=10), showlegend=False, yaxis_title="%")
        st.plotly_chart(fig4, use_container_width=True)

    c5, c6 = st.columns(2)

    with c5:
        st.markdown("#### Revenue vs Margin % *(per project, bubble)*")
        plot_df = fdf[["Revenue", "Margin Percentage Based On Overall Subtotal", "Client", "Service"]].dropna()
        fig5 = px.scatter(plot_df, x="Revenue", y="Margin Percentage Based On Overall Subtotal",
                          size=plot_df["Revenue"].clip(lower=1), hover_data=["Client", "Service"],
                          color_discrete_sequence=["rgba(139,126,232,0.7)"])
        fig5.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           height=380, margin=dict(t=10), showlegend=False,
                           xaxis_title="Revenue (₹)", yaxis_title="Margin %")
        st.plotly_chart(fig5, use_container_width=True)

    with c6:
        st.markdown("#### Invoice Status *(raised vs pending)*")
        inv_counts = fdf[pending_col].astype(str).str.strip().value_counts().reset_index()
        inv_counts.columns = ["Status", "Count"]
        fig6 = px.pie(inv_counts, names="Status", values="Count",
                      color_discrete_sequence=[PALETTE[1], PALETTE[0], PALETTE[2]])
        fig6.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", height=380,
                           legend=dict(orientation="h", yanchor="bottom", y=-0.3), margin=dict(t=10))
        st.plotly_chart(fig6, use_container_width=True)

    st.markdown("#### Client-wise Summary")
    client_summary = fdf.groupby("Client", as_index=False).agg(
        Projects=("Client", "count"),
        Revenue=("Revenue", "sum"),
        Margin=("Margin Amount Based On Overall Subtotal", "sum"),
    )
    client_summary["Margin %"] = (client_summary["Margin"] / client_summary["Revenue"] * 100).where(client_summary["Revenue"] != 0)
    client_summary = client_summary.sort_values("Revenue", ascending=False)
    display_summary = client_summary.copy()
    display_summary["Revenue"] = display_summary["Revenue"].apply(fmt_cr)
    display_summary["Margin"] = display_summary["Margin"].apply(fmt_cr)
    display_summary["Margin %"] = display_summary["Margin %"].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "-")
    st.dataframe(display_summary, use_container_width=True, hide_index=True, height=420)

# ========================================================================
# TAB 2: PROJECT-WISE
# ========================================================================
with tab2:
    neg_df = fdf[fdf["Margin Amount Based On Overall Subtotal"] < 0]
    pos_df = fdf[fdf["Margin Amount Based On Overall Subtotal"] > 0]

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Negative-Margin Projects", fmt_num(len(neg_df)))
    p2.metric("Positive-Margin Projects", fmt_num(len(pos_df)))
    p3.metric("Total Negative Margin", fmt_cr(neg_df["Margin Amount Based On Overall Subtotal"].sum()))
    p4.metric("Total Projects", fmt_num(len(fdf)))

    st.markdown("---")

    pc1, pc2 = st.columns([1.3, 1])

    with pc1:
        st.markdown("#### Negative-Margin Projects *(sorted worst first)*")
        neg_sorted = neg_df.sort_values("Margin Amount Based On Overall Subtotal").head(30)
        label_col = "Project Code Revenue Report" if neg_sorted["Project Code Revenue Report"].notna().any() else "Exam Name"
        fig7 = px.bar(neg_sorted, x="Margin Amount Based On Overall Subtotal", y=label_col, orientation="h",
                      color_discrete_sequence=["#e0665f"])
        fig7.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           yaxis=dict(autorange="reversed", tickfont=dict(size=9)), height=420, margin=dict(t=10),
                           showlegend=False, xaxis_title="Margin (₹)")
        st.plotly_chart(fig7, use_container_width=True)

    with pc2:
        st.markdown("#### Positive vs Negative *(project count)*")
        pn_counts = pd.DataFrame({"Type": ["Positive Margin", "Negative Margin"], "Count": [len(pos_df), len(neg_df)]})
        fig8 = px.pie(pn_counts, names="Type", values="Count", hole=0.55,
                      color_discrete_sequence=["#33b8a8", "#e0665f"])
        fig8.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", height=420,
                           legend=dict(orientation="h", yanchor="bottom", y=-0.3), margin=dict(t=10))
        st.plotly_chart(fig8, use_container_width=True)

    st.markdown("#### All Projects *(lowest margin first)*")
    search_col1, search_col2 = st.columns([3, 1])
    search_term = search_col1.text_input("Search project code, client or service...", "")
    neg_only = search_col2.checkbox("Show negative only")

    table_df = fdf.copy().sort_values("Margin Amount Based On Overall Subtotal")
    if neg_only:
        table_df = table_df[table_df["Margin Amount Based On Overall Subtotal"] < 0]
    if search_term:
        term = search_term.lower()
        mask = (
            table_df["Project Code Revenue Report"].astype(str).str.lower().str.contains(term, na=False)
            | table_df["Client"].astype(str).str.lower().str.contains(term, na=False)
            | table_df["Service"].astype(str).str.lower().str.contains(term, na=False)
        )
        table_df = table_df[mask]

    show_cols = ["Project Code Revenue Report", "Client", "Service", "MonthLabel",
                 "Revenue", "Margin Amount Based On Overall Subtotal", "Margin Percentage Based On Overall Subtotal"]
    show_cols = [c for c in show_cols if c in table_df.columns]
    display_table = table_df[show_cols].rename(columns={
        "Project Code Revenue Report": "Project Code",
        "MonthLabel": "Month",
        "Margin Amount Based On Overall Subtotal": "Margin (₹)",
        "Margin Percentage Based On Overall Subtotal": "Margin %",
    })
    st.caption(f"{len(display_table)} of {len(fdf)} projects")
    st.dataframe(display_table, use_container_width=True, hide_index=True, height=480)
