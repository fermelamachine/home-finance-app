import io
import pandas as pd
import streamlit as st

from engine import (
    load_rules,
    load_budgets,
    load_transactions_any,
    map_transactions,
    monthly_rollups,
    build_budget_variance,
    build_summary,
)

st.set_page_config(page_title="Home Finance Mapping + Budget", layout="wide")

st.title("Home Finance Engine (v1)")
st.caption("Deterministic rules mapping → review queue on ambiguity → monthly rollups + budget variance")

with st.sidebar:
    st.header("1) Load rules + budgets")
    rules_workbook = st.file_uploader("Upload your rules/budgets workbook (.xlsx)", type=["xlsx"])
    st.markdown("If you don't upload, the app can't map transactions (unless they already have mappings).")

    st.header("2) Load transactions")
    tx_files = st.file_uploader("Upload bank extracts (.xlsx or .csv) — you can select multiple", type=["xlsx", "csv"], accept_multiple_files=True)

    

    st.divider()
    st.markdown("**Rule behavior (locked):** case-insensitive substring match; 0 or 2+ matches go to Review Queue.")

@st.cache_data(show_spinner=False)
def _read_rules_budgets(xlsx_bytes: bytes):
    xl = pd.ExcelFile(io.BytesIO(xlsx_bytes))
    rules_df = pd.read_excel(xl, sheet_name="RULES")
    budgets_df = pd.read_excel(xl, sheet_name="BUDGETS")
    return rules_df, budgets_df

@st.cache_data(show_spinner=False)
def _read_tx_file(file_name: str, file_bytes: bytes):
    if file_name.lower().endswith(".csv"):
        return {"SHEET": pd.read_csv(io.BytesIO(file_bytes))}
    xl = pd.ExcelFile(io.BytesIO(file_bytes))
    out = {}
    for s in xl.sheet_names:
        out[s] = pd.read_excel(xl, sheet_name=s)
    return out

rules = []
budgets_df = None
if rules_workbook is not None:
    rules_df, budgets_raw = _read_rules_budgets(rules_workbook.getvalue())
    rules = load_rules(rules_df)
    budgets_df = load_budgets(budgets_raw)

tx_frames = []
if tx_files:
    for f in tx_files:
        sheets = _read_tx_file(f.name, f.getvalue())
        for sheet_name, df in sheets.items():
            # Attempt to infer account from sheet name if missing
            account_fb = sheet_name.upper()
            try:
                tx_frames.append(load_transactions_any(df, account_fallback=account_fb))
            except Exception:
                # If sheet isn't a transaction sheet, ignore quietly
                continue

if tx_frames:
    tx_all = pd.concat(tx_frames, ignore_index=True)
else:
    tx_all = pd.DataFrame(columns=["date","description","debit","credit","account","month","budget_category","category_detail"])

# Determine category list for sidebar controls
if "mapped_budget_category" in tx_all.columns and len(tx_all):
    pass

if budgets_df is not None:
    cats = sorted([c for c in budgets_df["BUDGET CATEGORY"].unique() if str(c).strip() != ""])
else:
    cats = sorted([c for c in tx_all.get("budget_category", pd.Series(dtype=str)).unique() if str(c).strip() != ""])

# Update sidebar multiselect options (Streamlit limitation workaround)
with st.sidebar:
    income_categories = st.multiselect("Income categories", options=cats, default=[c for c in ["PAYROLL","OTHER INCOME"] if c in cats])
    transfer_categories = st.multiselect("Transfer categories (exclude from totals)", options=cats, default=[c for c in ["PAYMENT","TRANSFERS","TRANSFER CHQ","INTERNAL BANK"] if c in cats])

if tx_all.empty:
    st.info("Upload transactions to begin.")
    st.stop()

mapped = map_transactions(tx_all, rules)

# Month selector
months = sorted(mapped["month"].unique().tolist())
month = st.selectbox("Select month", months, index=len(months)-1)

# Review queue
c1, c2 = st.columns([2, 1], gap="large")

with c1:
    st.subheader("Review Queue (Unmapped / Ambiguous)")
    rq = mapped[(mapped["month"] == month) & (mapped["mapping_status"] != "MAPPED")].copy()
    rq = rq.sort_values(["mapping_status","date"])
    st.write(f"{len(rq)} transaction(s) need review for {month}.")
    st.dataframe(rq[["date","account","description","debit","credit","mapping_status","mapping_candidates"]], use_container_width=True, height=320)

with c2:
    st.subheader("Mapped summary")
    summary = build_summary(mapped, month, income_categories, transfer_categories)
    st.metric("Total Income", f"${summary['TOTAL INCOME']:,.2f}")
    st.metric("Total Spending", f"${summary['TOTAL SPENDING']:,.2f}")
    st.metric("Discretionary", f"${summary['DISCRETIONARY']:,.2f}")

st.divider()

rollups = monthly_rollups(mapped, month)
by_cat = rollups["by_category"]
by_detail = rollups["by_detail"]

left, right = st.columns(2, gap="large")
with left:
    st.subheader("Spending / Income by Category (Net = Credit - Debit)")
    st.dataframe(by_cat, use_container_width=True, height=420)

with right:
    st.subheader("By Category Detail (Net = Credit - Debit)")
    st.dataframe(by_detail, use_container_width=True, height=420)

st.divider()

if budgets_df is not None:
    st.subheader("Budget vs Actual (Category-level parent budgets)")
    variance = build_budget_variance(budgets_df, by_cat.rename(columns={"BUDGET CATEGORY":"BUDGET CATEGORY"}))
    st.dataframe(variance, use_container_width=True, height=520)

    # Downloads
    st.subheader("Exports")
    colA, colB, colC = st.columns(3)
    with colA:
        st.download_button("Download mapped transactions (CSV)", mapped.to_csv(index=False).encode("utf-8"), file_name=f"mapped_transactions_{month}.csv", mime="text/csv")
    with colB:
        st.download_button("Download category rollup (CSV)", by_cat.to_csv(index=False).encode("utf-8"), file_name=f"category_rollup_{month}.csv", mime="text/csv")
    with colC:
        st.download_button("Download budget variance (CSV)", variance.to_csv(index=False).encode("utf-8"), file_name=f"budget_variance_{month}.csv", mime="text/csv")
else:
    st.warning("Upload the rules/budgets workbook to see Budget vs Actual and to enable mapping via rules.")
