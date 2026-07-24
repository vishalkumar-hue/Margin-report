"""
Margin Dashboard - Live Streamlit App (exact original UI + full filter set)
Fetches live data from a public Google Sheet, cleans it, and sends
row-level data to the ORIGINAL Chart.js HTML/CSS dashboard, which now
does all filtering + aggregation client-side across ALL tabs
(Overview, Monthly Trend, Client Analysis, Service Analysis, Status Overview,
Comparison, Quarter Comparison, Project-wise, Subtotal Details).
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
DEFAULT_SHEET_NAME = "Final Data"
TEMPLATE_PATH = Path(__file__).parent / "assets" / "dashboard_template.html"

st.set_page_config(page_title="Margin Dashboard", layout="wide", page_icon="📊", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.stApp{background:#0b1220;}
[data-testid="collapsedControl"]{display:none;}
section[data-testid="stSidebar"]{display:none;}
div.block-container{padding-top:3.5rem;}
div.stButton > button{
  background:#17233a; color:#e7ecf5; border:1px solid #223252; border-radius:6px;
}
div.stButton > button:hover{border-color:#d9a441; color:#d9a441;}
</style>
""", unsafe_allow_html=True)

sheet_id = DEFAULT_SHEET_ID
sheet_name = DEFAULT_SHEET_NAME

_, refresh_col = st.columns([8, 1])
with refresh_col:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# ----------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------
def build_csv_url(sheet_id: str, sheet_name: str) -> str:
    encoded_name = urllib.parse.quote(sheet_name)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_name}"


@st.cache_data(ttl=60, show_spinner="Google Sheet se data la raha hu...")
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


# ----------------------------------------------------------------------
# MANUAL COLUMN OVERRIDES (most reliable fix when name-matching keeps failing)
# ----------------------------------------------------------------------
# The "Final Data" sheet has SEVERAL columns that share the same header text
# (e.g. two columns literally titled "Service" and two titled "Month"). When
# that CSV is loaded, pandas silently renames the second occurrence to
# "Service.1" / "Month.1" - so plain name-matching below can only ever find
# the FIRST occurrence, even when the second one is the one you actually want.
#
# Fixed so far (verified against Final_Margin_Report_-_Final_Data.csv):
#   - "Month"   -> column AT ("Apr_26" short form)
#   - "Service" -> column AQ (human-readable duplicate)
#   - "OpGuardActualCamVoipNode" -> column BE
#   - "ProjectCode" -> column AU
#
# STILL SUSPECT (Subtotal tab showing blank/zero data - likely the SAME
# duplicate-header bug): "TotalCandidate", "OverallSubtotal", "SubtotalOps".
# Run the app, open the "🔍 Debug: Subtotal tab column resolution" expander
# right below the KPI area, check which sheet column each one resolved to
# and whether the sample values look right. Then fill in the correct column
# LETTER below (open the sheet, look at the column letter above the header).
#
# IMPORTANT: these are POSITION-based. If columns get added/removed/reordered
# in the sheet later, re-check the letter for each field and update below.
#
# Leave a value as "" to fall back to automatic name-based matching instead.
MANUAL_COLUMN_OVERRIDES = {
    "Month": "AT",
    "Service": "AQ",
    "OpGuardActualCamVoipNode": "BE",
    "ProjectCode": "AU",
    # "TotalCandidate": "",     # <-- fill in after checking debug output
    # "OverallSubtotal": "",    # <-- fill in after checking debug output
    # "SubtotalOps": "",        # <-- fill in after checking debug output
}


def col_letter_to_index(letter: str) -> int:
    """Convert a Sheets/Excel-style column letter ('A', 'U', 'AB', ...) to a 0-based index."""
    letter = letter.strip().upper()
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def find_col(df: pd.DataFrame, target: str):
    """Fuzzy-match a column name ignoring case, whitespace, and punctuation
    (spaces, slashes, dashes, etc.), since sheet headers sometimes have
    stray spaces, different slash/dash spacing, or slightly different
    casing than what's typed here (e.g. 'Biling Status' vs 'Billing Status',
    or 'Op / Guard Actual Count / Cam Count / VOIP / Node' vs
    'Op/Guard Actual Count/Cam Count/VOIP/Node').
    NOTE: when a header is duplicated in the sheet, pandas renames the 2nd+
    occurrence to 'Header.1', 'Header.2', etc - those will NOT strict-match
    the plain target name here, only the first occurrence will. Use
    MANUAL_COLUMN_OVERRIDES (column letter) when you specifically need a
    later duplicate."""
    def normalize(s: str) -> str:
        return "".join(ch for ch in s.strip().lower() if ch.isalnum())

    target_norm = normalize(target)
    for c in df.columns:
        if normalize(c) == target_norm:
            return c
    return None


def find_col_loose(df: pd.DataFrame, target: str):
    """Looser fallback for stubborn headers: matches a column that contains
    ALL the significant words from target (3+ letters), in any order,
    ignoring exact punctuation/spacing/duplicated-name suffixes pandas adds
    (e.g. 'Foo.1'). Used only when the strict find_col match fails."""
    import re
    words = [w for w in re.split(r"[^a-z0-9]+", target.lower()) if len(w) > 2]
    if not words:
        return None
    for c in df.columns:
        c_norm = re.sub(r"[^a-z0-9]+", "", c.lower())
        if all(w in c_norm for w in words):
            return c
    return None


# Logical field -> possible sheet header(s) to try, in order.
COLUMN_TARGETS = {
    "ProjectCode": ["Project Code Revenue Report", "Project Code", "Correct Project Code"],
    "Client": ["Client"],
    "ClientSSC": ["Client (With SSC)"],
    "Service": ["Service"],
    "FY": ["FY", "Financial Year"],
    "Quarter": ["Quater", "Quarter", "Qtr"],
    "Month": ["Month"],
    "ExamStartDate": ["Exam Start Date", "Exam Date", "Start Date"],
    "ExamNameDate": ["Exam Name & Date", "Exam Name"],
    "ProjectStatus": ["Project Status"],
    "InvoiceStatus": ["Invoice Status"],
    "ReviewInvoice": ["Review Based on Invoice (Raised/Pending)"],
    "BillingType": ["Billing Type"],
    "BillingStatus": ["Status", "Billing Status", "Biling Status"],
    "Revenue": ["Revenue"],
    "Margin": ["Margin Amount Based On Overall Subtotal"],
    "MarginPct": ["Margin % - Overall Subtotal", "Margin Percentage Based On Overall Subtotal"],
    "MarginPctOps": ["Margin % - Overall Subtotal-OPs"],
    "BioFriDimensioning": ["BIO & Fri Dimensioning", "Bio & Fri Dimensioning"],
    # --- newly added columns for the "All Projects" table ---
    "CentreCount": ["Centre Count", "Center Count"],
    "TotalCandidate": ["Total Candidate", "Total Candidates"],
    "MaxCandidate": ["Max Candidate", "Max Candidates"],
    "OpGuardActualCamVoipNode": [
        "Op/Guard Actual Count/Cam Count/VOIP/Node",
        "Op/Guard Actual Count / Cam Count / VOIP / Node",
        "Op/Guard Actual Count/Cam Count/VOIP/Node ",
        "Op/Guard Actual Count/Cam Count/VOIP",
    ],
    # --- newly added columns for the "Subtotal Details" tab ---
    "OverallSubtotal": ["Overall Subtotal", "Subtotal Overall", "Subtotal"],
    "SubtotalOps": ["Subtotal (Ops)", "Subtotal(Ops)", "Subtotal Ops", "Subtotal - Ops"],
}


def resolve_columns(df: pd.DataFrame):
    """Returns {logical_name: actual_sheet_column_or_None}."""
    resolved = {}
    for logical, candidates in COLUMN_TARGETS.items():
        # 1) manual position override wins if one is set for this field
        override_letter = MANUAL_COLUMN_OVERRIDES.get(logical, "")
        if override_letter:
            idx = col_letter_to_index(override_letter)
            if 0 <= idx < len(df.columns):
                resolved[logical] = df.columns[idx]
                continue

        # 2) strict name match
        found = None
        for cand in candidates:
            found = find_col(df, cand)
            if found:
                break
        # 3) loose keyword-based fallback match
        if not found:
            for cand in candidates:
                found = find_col_loose(df, cand)
                if found:
                    break
        resolved[logical] = found
    return resolved


# Numeric fields get comma/₹/% stripped and converted to float.
# CentreCount / TotalCandidate / MaxCandidate are plain counts, so they go here too.
# OverallSubtotal / SubtotalOps are rupee amounts, also numeric.
# OpGuardActualCamVoipNode is left out - it's a combined text field (Op/Guard/Cam/VOIP/Node
# all in one cell), so it stays as text.
NUMERIC_FIELDS = {
    "Revenue", "Margin", "MarginPct", "MarginPctOps",
    "CentreCount", "TotalCandidate", "MaxCandidate",
    "OverallSubtotal", "SubtotalOps",
}


def prepare_data(raw: pd.DataFrame):
    df = raw.copy()
    cols = resolve_columns(df)

    # Clean numeric fields where the source column exists
    for logical in NUMERIC_FIELDS:
        src = cols.get(logical)
        if src:
            df[src] = clean_numeric(df[src])

    revenue_col = cols.get("Revenue")
    margin_col = cols.get("Margin")
    marginpct_col = cols.get("MarginPct")

    # Fallback margin % if blank but revenue+margin amount present
    if revenue_col and margin_col and marginpct_col:
        mask = df[marginpct_col].isna() & df[revenue_col].notna() & (df[revenue_col] != 0)
        df.loc[mask, marginpct_col] = df.loc[mask, margin_col] / df.loc[mask, revenue_col] * 100
        zero_rev_mask = df[revenue_col].isna() | (df[revenue_col] == 0)
        df.loc[zero_rev_mask, marginpct_col] = df.loc[zero_rev_mask, marginpct_col].fillna(0)

    client_col = cols.get("Client")
    if client_col:
        df[client_col] = df[client_col].astype(str).str.strip()
    if cols.get("Service"):
        df[cols["Service"]] = df[cols["Service"]].astype(str).str.strip()
    if cols.get("Month"):
        df[cols["Month"]] = df[cols["Month"]].astype(str).str.strip()

    # Drop fully blank rows (no client, no revenue)
    if client_col and revenue_col:
        df = df[~((df[client_col].isin(["", "nan", "None"])) & df[revenue_col].isna())]

    # ProjectCode fallback if the sheet column is missing/blank
    pc_col = cols.get("ProjectCode")
    exam_col = cols.get("ExamNameDate")
    service_col = cols.get("Service")
    if pc_col:
        pc = df[pc_col].astype(str).str.strip()
    else:
        pc = pd.Series([""] * len(df), index=df.index)
    fallback_parts = []
    if client_col:
        fallback_parts.append(df[client_col].astype(str))
    if exam_col:
        fallback_parts.append(df[exam_col].astype(str))
    if service_col:
        fallback_parts.append(df[service_col].astype(str))
    if fallback_parts:
        fallback = fallback_parts[0]
        for part in fallback_parts[1:]:
            fallback = fallback + "/" + part
        fallback = fallback.str.replace(" ", "", regex=False)
    else:
        fallback = pd.Series([f"ROW{i}" for i in range(len(df))], index=df.index)
    df["_ProjectCode"] = pc.where(~pc.isin(["", "nan", "None"]), fallback)

    quarter_col = cols.get("Quarter")
    exam_start_col = cols.get("ExamStartDate")
    if quarter_col:
        df["_Quarter"] = df[quarter_col].fillna("").astype(str).str.strip()
    elif exam_start_col:
        df["_Quarter"] = derive_quarter(df[exam_start_col])
    else:
        df["_Quarter"] = ""

    return df, cols


def derive_quarter(series: pd.Series) -> pd.Series:
    """Quarter label (e.g. 'Q1-2026') computed from a date column,
    since the sheet doesn't have a dedicated Quarter column."""
    dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
    out = []
    for d in dt:
        if pd.isna(d):
            out.append("")
        else:
            out.append(f"Q{d.quarter}-{d.year}")
    return pd.Series(out, index=series.index)


def month_sort_key(m: str):
    try:
        return datetime.strptime(m, "%b_%y")
    except Exception:
        return datetime.max


def _series_or_blank(df, cols, logical, length):
    col = cols.get(logical)
    if col:
        return df[col].fillna("").astype(str)
    return pd.Series([""] * length, index=df.index)


def build_rows(df: pd.DataFrame, cols: dict):
    n = len(df)
    revenue = df[cols["Revenue"]] if cols.get("Revenue") else pd.Series([0.0] * n, index=df.index)
    margin = df[cols["Margin"]] if cols.get("Margin") else pd.Series([0.0] * n, index=df.index)
    marginpct = df[cols["MarginPct"]] if cols.get("MarginPct") else pd.Series([0.0] * n, index=df.index)
    marginpct_ops = df[cols["MarginPctOps"]] if cols.get("MarginPctOps") else pd.Series([None] * n, index=df.index)

    # numeric count columns
    centre_count = df[cols["CentreCount"]] if cols.get("CentreCount") else pd.Series([0.0] * n, index=df.index)
    total_candidate = df[cols["TotalCandidate"]] if cols.get("TotalCandidate") else pd.Series([0.0] * n, index=df.index)
    max_candidate = df[cols["MaxCandidate"]] if cols.get("MaxCandidate") else pd.Series([0.0] * n, index=df.index)

    # newly added: Overall Subtotal / Subtotal (Ops) for the Subtotal Details tab
    overall_subtotal = df[cols["OverallSubtotal"]] if cols.get("OverallSubtotal") else pd.Series([0.0] * n, index=df.index)
    subtotal_ops = df[cols["SubtotalOps"]] if cols.get("SubtotalOps") else pd.Series([0.0] * n, index=df.index)

    # --- Per Candidate / Per Camera-OP-Guard subtotal rate fields, used by
    # the Subtotal Details tab KPIs + table (previously referenced in the
    # HTML/JS as p.perCandidateSubtotalOverall etc. but NEVER computed here
    # -> this was ALSO causing blank data in those columns, on top of the
    # duplicate-header column-resolution issue). Guard against
    # divide-by-zero with .replace(0, pd.NA).
    safe_total_candidate = total_candidate.replace(0, pd.NA)
    per_candidate_subtotal_overall = (overall_subtotal / safe_total_candidate).fillna(0)
    per_candidate_subtotal_ops = (subtotal_ops / safe_total_candidate).fillna(0)

    # "Per Camera/OP/Guard" denominator: centre_count is used as the closest
    # numeric proxy since opGuardData is a combined text field, not a count.
    safe_centre_count = centre_count.replace(0, pd.NA)
    per_camera_opguard_subtotal_overall = (overall_subtotal / safe_centre_count).fillna(0)
    per_camera_opguard_subtotal_ops = (subtotal_ops / safe_centre_count).fillna(0)

    out = pd.DataFrame({
        "projectCode": df["_ProjectCode"],
        "client": _series_or_blank(df, cols, "Client", n),
        "clientSSC": _series_or_blank(df, cols, "ClientSSC", n),
        "service": _series_or_blank(df, cols, "Service", n),
        "fy": _series_or_blank(df, cols, "FY", n),
        "month": _series_or_blank(df, cols, "Month", n),
        "quarter": df["_Quarter"].fillna("").astype(str),
        "examNameDate": _series_or_blank(df, cols, "ExamNameDate", n),
        "projectStatus": _series_or_blank(df, cols, "ProjectStatus", n),
        "invoiceStatus": _series_or_blank(df, cols, "InvoiceStatus", n),
        "reviewInvoice": _series_or_blank(df, cols, "ReviewInvoice", n),
        "billingType": _series_or_blank(df, cols, "BillingType", n),
        "billingStatus": _series_or_blank(df, cols, "BillingStatus", n),
        "bioFriDimensioning": _series_or_blank(df, cols, "BioFriDimensioning", n),
        "revenue": revenue.fillna(0),
        "margin": margin.fillna(0),
        "marginPct": marginpct.fillna(0),
        "marginPctOps": marginpct_ops,
        # fields for the "All Projects" table
        "centreCount": centre_count.fillna(0),
        "totalCandidate": total_candidate.fillna(0),
        "maxCandidate": max_candidate.fillna(0),
        "opGuardData": _series_or_blank(df, cols, "OpGuardActualCamVoipNode", n),
        # fields for the "Subtotal Details" tab
        "overallSubtotal": overall_subtotal.fillna(0),
        "subtotalOps": subtotal_ops.fillna(0),
        "perCandidateSubtotalOverall": per_candidate_subtotal_overall,
        "perCandidateSubtotalOps": per_candidate_subtotal_ops,
        "perCameraOpGuardSubtotalOverall": per_camera_opguard_subtotal_overall,
        "perCameraOpGuardSubtotalOps": per_camera_opguard_subtotal_ops,
    })
    return out.to_dict("records")


# ----------------------------------------------------------------------
# LOAD + BUILD
# ----------------------------------------------------------------------
try:
    raw_df = load_raw_data(sheet_id, sheet_name)
    prepared_df, resolved_cols = prepare_data(raw_df)
except Exception as e:
    st.error(f"Sheet load nahi ho payi. Sharing settings aur tab name check karo. Error: {e}")
    st.stop()

if prepared_df.empty:
    st.warning("Sheet se koi valid row nahi mili. Column headers check karo.")
    st.stop()

# # --- TEMP DEBUG: dekho konse actual sheet column resolve ho rahe hai for
# # the 4 Subtotal-tab fields. Remove this block once you've confirmed the
# # correct MANUAL_COLUMN_OVERRIDES letters above and the tab shows data. ---
# with st.expander("🔍 Debug: Subtotal tab column resolution", expanded=False):
#     for field in ["TotalCandidate", "OverallSubtotal", "SubtotalOps", "OpGuardActualCamVoipNode"]:
#         actual_col = resolved_cols.get(field)
#         st.write(f"**{field}** → resolved to sheet column: `{actual_col}`")
#         if actual_col:
#             st.write(prepared_df[actual_col].head(5).tolist())
#         else:
#             st.write("❌ NOT FOUND (name-matching failed - needs a MANUAL_COLUMN_OVERRIDES letter)")
# # --- END DEBUG ---

rows = build_rows(prepared_df, resolved_cols)

months_present = sorted(
    {r["month"] for r in rows if r["month"]},
    key=month_sort_key,
)
period_label = f"{months_present[0]} – {months_present[-1]}" if months_present else ""

template_html = TEMPLATE_PATH.read_text(encoding="utf-8")
final_html = (
    template_html
    .replace("__ROWS_JSON__", json.dumps(rows, default=str))
    .replace("__MONTH_ORDER_JSON__", json.dumps(months_present, default=str))
    .replace("__PERIOD_LABEL__", period_label)
)

components.html(final_html, height=3200, scrolling=True)
