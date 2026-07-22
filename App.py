"""
Margin Dashboard - Live Streamlit App (exact original UI)
Fetches live data from a public Google Sheet, computes the same
aggregates as the original static dashboard, and renders the
ORIGINAL Chart.js HTML/CSS design via an embedded component.
"""

import json
import urllib.parse
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
DEFAULT_SHEET_ID = "1u722Jf7tTX5l195AHxSU_fMHOQdZacoeAlLGqmgdPFc"
DEFAULT_SHEET_NAME = "Raw Data"
TEMPLATE_PATH = Path(__file__).parent / "assets" / "dashboard_template.html"

st.set_page_config(page_title="Margin Dashboard", layout="wide", page_icon="📊")
st.markdown("<style>.stApp{background:#0b1220;}</style>", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------------------
st.sidebar.header("⚙️ Data Source")
sheet_id = st.sidebar.text_input("Google Sheet ID", value=DEFAULT_SHEET_ID)
sheet_name = st.sidebar.text_input("Tab name", value=DEFAULT_SHEET_NAME)

refresh_seconds = st.sidebar.slider("Auto-refresh every (seconds)", min_value=10, max_value=120, value=20, step=5)

if st.sidebar.button("🔄 Force refresh now"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption("Sheet must stay shared as 'Anyone with link can view'. Har refresh pe latest sheet data khinchta hai.")
st.sidebar.caption(f"Last loaded: {datetime.now().strftime('%d %b %Y, %I:%M:%S %p')}")

# ----------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------
def build_csv_url(sheet_id: str, sheet_name: str) -> str:
    encoded_name = urllib.parse.quote(sheet_name)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_name}"


@st.cache_data(ttl=15, show_spinner="Google Sheet se live data la raha hu...")
def load_raw_data(sheet_id: str, sheet_name: str) -> pd.DataFrame:
    url = build_csv_url(sheet_id, sheet_name)
    df = pd.read_csv(url)
    df.columns = [c.strip() for c in df.columns]
    return df


def clean_numeric(series: pd.Series) -> pd.Series:
    if series.dtype.kind in "if":
        return series.astype(float)
    cleaned = (
        series.astype(str)
        .str.replace(r"[₹,%\s]", "", regex=True)
        .replace({"": None, "nan": None, "None": None, "-": None})
    )
    return pd.to_numeric(cleaned, errors="coerce")


REQUIRED_COLS = [
    "Client", "Service", "Month", "Exam Name", "Project Status",
    "Revenue", "Margin Amount Based On Overall Subtotal",
    "Margin Percentage Based On Overall Subtotal",
    "Project Code Revenue Report",
    "Invoice Status", "Review Based on Invoice (Raised/Pending)",
]


def prepare_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = None

    for c in ["Revenue", "Margin Amount Based On Overall Subtotal", "Margin Percentage Based On Overall Subtotal"]:
        df[c] = clean_numeric(df[c])

    # Fallback margin % if blank but revenue+margin amount present
    mask = df["Margin Percentage Based On Overall Subtotal"].isna() & df["Revenue"].notna() & (df["Revenue"] != 0)
    df.loc[mask, "Margin Percentage Based On Overall Subtotal"] = (
        df.loc[mask, "Margin Amount Based On Overall Subtotal"] / df.loc[mask, "Revenue"] * 100
    )
    # Where revenue is 0/blank, force margin% to 0 to avoid inf/NaN in charts
    zero_rev_mask = df["Revenue"].isna() | (df["Revenue"] == 0)
    df.loc[zero_rev_mask, "Margin Percentage Based On Overall Subtotal"] = df.loc[
        zero_rev_mask, "Margin Percentage Based On Overall Subtotal"
    ].fillna(0)

    df["Month"] = df["Month"].astype(str).str.strip()
    df["Client"] = df["Client"].astype(str).str.strip()
    df["Service"] = df["Service"].astype(str).str.strip()

    # Drop fully blank rows
    df = df[~((df["Client"].isin(["", "nan", "None"])) & df["Revenue"].isna())]

    # ProjectCode: prefer sheet column, else build a fallback
    pc = df["Project Code Revenue Report"].astype(str).str.strip()
    fallback = (df["Client"] + "/" + df["Exam Name"].astype(str) + "/" + df["Service"]).str.replace(" ", "", regex=False)
    df["ProjectCode"] = pc.where(~pc.isin(["", "nan", "None"]), fallback)

    # Invoice status: prefer the review column, else Invoice Status
    review = df["Review Based on Invoice (Raised/Pending)"].astype(str).str.strip()
    invstat = df["Invoice Status"].astype(str).str.strip()
    df["InvoiceStatusFinal"] = review.where(~review.isin(["", "nan", "None"]), invstat)

    return df


def month_sort_key(m: str):
    try:
        return datetime.strptime(m, "%b_%y")
    except Exception:
        return datetime.max


def build_dashboard_json(df: pd.DataFrame):
    # ---- monthly ----
    monthly_g = df.groupby("Month", as_index=False).agg(
        Revenue=("Revenue", "sum"),
        Margin=("Margin Amount Based On Overall Subtotal", "sum"),
        Projects=("Month", "count"),
    )
    monthly_g["MarginPct"] = (monthly_g["Margin"] / monthly_g["Revenue"] * 100).where(monthly_g["Revenue"] != 0, 0)
    monthly_g = monthly_g.sort_values("Month", key=lambda s: s.map(month_sort_key))
    monthly = monthly_g.to_dict("records")

    # ---- client ----
    client_g = df.groupby("Client", as_index=False).agg(
        Revenue=("Revenue", "sum"),
        Margin=("Margin Amount Based On Overall Subtotal", "sum"),
        Projects=("Client", "count"),
    )
    client_g["MarginPct"] = (client_g["Margin"] / client_g["Revenue"] * 100).where(client_g["Revenue"] != 0, 0)
    client = client_g.to_dict("records")

    # ---- service ----
    service_g = df.groupby("Service", as_index=False).agg(
        Revenue=("Revenue", "sum"),
        Margin=("Margin Amount Based On Overall Subtotal", "sum"),
        Projects=("Service", "count"),
    )
    service_g["MarginPct"] = (service_g["Margin"] / service_g["Revenue"] * 100).where(service_g["Revenue"] != 0, 0)
    service = service_g.to_dict("records")

    # ---- project status ----
    pstatus_g = df.groupby("Project Status", as_index=False).agg(
        Revenue=("Revenue", "sum"), Count=("Project Status", "count")
    )
    pstatus_g = pstatus_g.rename(columns={"Project Status": "Project Status"})
    pstatus = [{"Project Status": r["Project Status"], "Revenue": r["Revenue"], "Count": r["Count"]}
               for r in pstatus_g.to_dict("records")]

    # ---- invoice status ----
    istatus_g = df.groupby("InvoiceStatusFinal", as_index=False).size()
    istatus = [{"Invoice Status": r["InvoiceStatusFinal"], "Count": r["size"]} for r in istatus_g.to_dict("records")]

    # ---- scatter (row level, for bubble chart + KPI averages) ----
    scatter_df = df[["Client", "Exam Name", "Revenue", "Margin Percentage Based On Overall Subtotal", "Month", "Service"]].copy()
    scatter_df = scatter_df.rename(columns={"Margin Percentage Based On Overall Subtotal": "MarginPct"})
    scatter_df = scatter_df.dropna(subset=["Revenue"])
    scatter = scatter_df.to_dict("records")

    data_obj = {
        "monthly": monthly,
        "client": client,
        "service": service,
        "pstatus": pstatus,
        "istatus": istatus,
        "scatter": scatter,
    }

    # ---- project-level rows (Project-wise tab) ----
    proj_df = df[["ProjectCode", "Client", "Service", "Month", "Revenue",
                  "Margin Amount Based On Overall Subtotal", "Margin Percentage Based On Overall Subtotal"]].copy()
    proj_df = proj_df.rename(columns={
        "Margin Amount Based On Overall Subtotal": "Margin",
        "Margin Percentage Based On Overall Subtotal": "MarginPct",
    })
    proj_df = proj_df.dropna(subset=["Margin"])
    proj_df = proj_df.sort_values("Margin")
    project_data = proj_df.to_dict("records")

    return data_obj, project_data


# ----------------------------------------------------------------------
# LOAD + BUILD
# ----------------------------------------------------------------------
try:
    raw_df = load_raw_data(sheet_id, sheet_name)
    df = prepare_data(raw_df)
except Exception as e:
    st.error(f"Sheet load nahi ho payi. Sharing settings aur tab name check karo. Error: {e}")
    st.stop()

if df.empty:
    st.warning("Sheet se koi valid row nahi mili. Column headers check karo.")
    st.stop()

data_obj, project_data = build_dashboard_json(df)

months_present = sorted(df["Month"].dropna().unique().tolist(), key=month_sort_key)
period_label = f"{months_present[0]} – {months_present[-1]}" if months_present else ""

template_html = TEMPLATE_PATH.read_text(encoding="utf-8")
final_html = (
    template_html
    .replace("__DATA_JSON__", json.dumps(data_obj, default=str))
    .replace("__PROJECT_JSON__", json.dumps(project_data, default=str))
    .replace("__PERIOD_LABEL__", period_label)
)

# Auto-refresh the whole Streamlit page (reruns Python -> re-fetches sheet
# once the 15s cache expires) so data stays near-live without extra packages.
refresh_ms = int(refresh_seconds * 1000)
components.html(f"<script>setTimeout(function(){{ window.parent.location.reload(); }}, {refresh_ms});</script>", height=0)

components.html(final_html, height=3200, scrolling=True)
