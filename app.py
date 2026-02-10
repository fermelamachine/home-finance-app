import base64
import io
import json
from typing import Optional, Tuple, Dict, List

import pandas as pd
import requests
import streamlit as st

from engine import (
    load_rules_from_excel,
    rules_to_dataframe,
    load_rules_from_csv,
    load_budgets_from_excel,
    load_budgets_from_csv,
    load_overrides_from_csv,
    load_transactions_any,
    map_transactions,
    monthly_rollups,
    build_budget_variance,
    build_summary,
    matched_keywords_for_rule,
)

st.set_page_config(page_title="Home Finance (GitHub DB)", layout="wide")
st.title("Home Finance App (GitHub DB)")
st.caption("Rules + budgets + approvals stored in your GitHub repo. Mapping is DESCRIPTION-only (account agnostic).")


def _s(key: str, default: str = "") -> str:
    """Read a secret and strip whitespace/newlines (prevents ref=m\\nain issues)."""
    try:
        return str(st.secrets.get(key, default)).strip()
    except Exception:
        return default


def secrets_ok() -> bool:
    return bool(_s("GITHUB_TOKEN")) and bool(_s("GITHUB_REPO"))


def gh_headers():
    return {"Authorization": f"token {_s('GITHUB_TOKEN')}", "Accept": "application/vnd.github+json"}




import base64
import requests
import streamlit as st

def gh_put_file(path: str, content_str: str, message: str, sha: str | None = None):
    token = st.secrets.get("GITHUB_TOKEN", "")
    owner = st.secrets.get("GITHUB_OWNER")
    repo  = st.secrets.get("GITHUB_REPO")
    branch = st.secrets.get("GITHUB_BRANCH", "main")

    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN in Streamlit secrets.")
    if not owner or not repo:
        raise RuntimeError("Missing GITHUB_OWNER / GITHUB_REPO in Streamlit secrets.")

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        "branch": branch,
    }

    # Only include sha when updating an existing file
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=headers, json=payload, timeout=30)

    if not r.ok:
        raise RuntimeError(
            "GitHub PUT failed\n"
            f"Status: {r.status_code}\n"
            f"URL: {url}\n"
            f"Response: {r.text}\n"
        )

    return r.json()


def gh_get_file(path: str):
    token = st.secrets.get("GITHUB_TOKEN", "")
    owner = st.secrets.get("GITHUB_OWNER")
    repo  = st.secrets.get("GITHUB_REPO")
    branch = st.secrets.get("GITHUB_BRANCH", "main")

    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN in Streamlit secrets.")
    if not owner or not repo:
        raise RuntimeError("Missing GITHUB_OWNER / GITHUB_REPO in Streamlit secrets.")

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    r = requests.get(url, headers=headers, timeout=30)

    # If the file doesn't exist yet, that's okay
    if r.status_code == 404:
        return None, None

    if not r.ok:
        raise RuntimeError(
            "GitHub GET failed\n"
            f"Status: {r.status_code}\n"
            f"URL: {url}\n"
            f"Response: {r.text}\n"
        )

    data = r.json()
    content_b64 = data.get("content", "") or ""
    sha = data.get("sha")

    decoded = base64.b64decode(content_b64).decode("utf-8") if content_b64 else ""
    return decoded, sha
    r.raise_for_status()
    return r.json()


RULES_PATH = "data/rules.csv"
BUDGETS_PATH = "data/budgets.csv"
OVERRIDES_PATH = "data/overrides.csv"

with st.sidebar:
    st.header("0) Setup")
    if not secrets_ok():
        st.error("Missing secrets. Streamlit Cloud → App → Settings → Secrets: add GITHUB_TOKEN and GITHUB_REPO.")
        st.stop()
    st.success("GitHub secrets present.")

    st.header("1) Rules/Budgets")
    init_upload = st.file_uploader("One-time init: upload RULES BUDGETS.xlsx", type=["xlsx"])
    st.caption("After init, rules/budgets load automatically from GitHub.")

    st.header("2) Transactions")
    tx_files = st.file_uploader("Upload bank extracts (.xlsx/.csv) — multiple allowed", type=["xlsx", "csv"], accept_multiple_files=True)

@st.cache_data(show_spinner=False)

def load_repo_texts():
    try:
        rules_text, _ = gh_get_file(RULES_PATH)
        budgets_text, _ = gh_get_file(BUDGETS_PATH)
        overrides_text, _ = gh_get_file(OVERRIDES_PATH)
        return {"rules": rules_text, "budgets": budgets_text, "overrides": overrides_text}
    except Exception as e:
        st.error(str(e))
        st.stop()


repo = load_repo_texts()

if init_upload is not None and (repo["rules"] is None or repo["budgets"] is None):
    xl = pd.ExcelFile(io.BytesIO(init_upload.getvalue()))
    rules_df = pd.read_excel(xl, sheet_name="RULES")
    budgets_df = pd.read_excel(xl, sheet_name="BUDGETS")

    rules = load_rules_from_excel(rules_df)
    rules_csv = rules_to_dataframe(rules).to_csv(index=False)
    budgets_csv = load_budgets_from_excel(budgets_df).to_csv(index=False)

    _, rules_sha = gh_get_file(RULES_PATH)
    _, budgets_sha = gh_get_file(BUDGETS_PATH)
    gh_put_file(RULES_PATH, rules_csv, "Initialize rules.csv", sha=rules_sha)
    gh_put_file(BUDGETS_PATH, budgets_csv, "Initialize budgets.csv", sha=budgets_sha)

    ov_text, _ = gh_get_file(OVERRIDES_PATH)
    if ov_text is None:
        gh_put_file(OVERRIDES_PATH, "KEYWORD,BUDGET CATEGORY,CATEGORY DETAIL\n", "Initialize overrides.csv", sha=None)

    st.sidebar.success("Initialized in GitHub. Reloading…")
    st.cache_data.clear()
    st.rerun()

if repo["rules"] is None or repo["budgets"] is None:
    st.info("Repo not initialized yet. Upload RULES BUDGETS.xlsx once (sidebar → Rules/Budgets).")
    st.stop()

rules = load_rules_from_csv(repo["rules"])
budgets_df = load_budgets_from_csv(repo["budgets"])
overrides = load_overrides_from_csv(repo["overrides"] or "KEYWORD,BUDGET CATEGORY,CATEGORY DETAIL\n")
rule_by_id = {r.rule_id: r for r in rules}

cats = sorted(set(list(budgets_df["BUDGET CATEGORY"].unique()) + [r.budget_category for r in rules if r.budget_category]))

with st.sidebar:
    income_default = [c for c in ["PAYROLL","OTHER INCOME"] if c in cats]
    transfer_default = [c for c in ["PAYMENT","TRANSFERS","TRANSFER CHQ","INTERNAL BANK","HELOC PYMNT"] if c in cats]
    income_categories = st.multiselect("Income categories", options=cats, default=income_default, key="income_cats")
    transfer_categories = st.multiselect("Transfer categories (exclude from spending)", options=cats, default=transfer_default, key="transfer_cats")

@st.cache_data(show_spinner=False)
def read_tx(files) -> pd.DataFrame:
    frames = []
    for f in files:
        if f.name.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(f.getvalue()))
            frames.append(load_transactions_any(df, account_fallback="CSV"))
        else:
            xl = pd.ExcelFile(io.BytesIO(f.getvalue()))
            for s in xl.sheet_names:
                try:
                    df = pd.read_excel(xl, sheet_name=s)
                    frames.append(load_transactions_any(df, account_fallback=s.upper()))
                except Exception:
                    continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

if not tx_files:
    st.info("Upload transactions to begin.")
    st.stop()

tx_all = read_tx(tx_files)
mapped = map_transactions(tx_all, rules, overrides=overrides)

months = sorted(mapped["month"].unique().tolist())
month = st.selectbox("Select month", months, index=len(months)-1)

m_this = mapped[mapped["month"] == month].copy()
counts = m_this["mapping_status"].value_counts()
unmapped_count = int(counts.get("UNMAPPED_NO_MATCH", 0) + counts.get("UNMAPPED_MULTI_MATCH", 0))
picked_count = int(counts.get("REVIEW_PICKED", 0))

c1, c2 = st.columns([2, 1], gap="large")
with c1:
    st.subheader("Review Queue")
    rq = m_this[m_this["mapping_status"] != "MAPPED"].copy().sort_values(["mapping_status","date"])
    rq = rq.reset_index(drop=True)
    rq["RQ_ID"] = rq.index
    st.write(f"For {month}: **Unmapped {unmapped_count}**, **REVIEW_PICKED {picked_count}**")
    cols = ["RQ_ID","date","account","description","debit","credit","mapping_status","override_status","mapping_candidates","mapped_budget_category","mapped_category_detail"]
    cols = [c for c in cols if c in rq.columns]
    st.dataframe(rq[cols], use_container_width=True, height=360)

with c2:
    st.subheader("Totals")
    summary = build_summary(mapped, month, income_categories, transfer_categories)
    st.metric("Total Income", f"${summary['TOTAL INCOME']:,.2f}")
    st.metric("Total Spending", f"${summary['TOTAL SPENDING']:,.2f}")
    st.metric("Discretionary", f"${summary['DISCRETIONARY']:,.2f}")

st.divider()
rollups = monthly_rollups(mapped, month)
l, r = st.columns(2, gap="large")
with l:
    st.subheader("By Category (Net = Credit - Debit)")
    st.dataframe(rollups["by_category"], use_container_width=True, height=420)
with r:
    st.subheader("By Category Detail (Net = Credit - Debit)")
    st.dataframe(rollups["by_detail"], use_container_width=True, height=420)

st.divider()
st.subheader("Budget vs Actual")
variance = build_budget_variance(budgets_df, rollups["by_category"])
st.dataframe(variance, use_container_width=True, height=520)

st.divider()
st.subheader("Resolve one review item (adds an override in GitHub so you won't see it again)")

if rq.empty:
    st.success("Nothing to resolve for this month.")
    st.stop()

rq_id = st.number_input("Enter RQ_ID from the Review Queue table", min_value=0, max_value=int(rq["RQ_ID"].max()), step=1)
row = rq.loc[rq["RQ_ID"] == rq_id].iloc[0]
desc = str(row["description"])
desc_lower = desc.lower()

st.code(f"{row['date'].date()} | {row.get('account','')} | {desc}\nDT={row['debit']}  CR={row['credit']}  STATUS={row['mapping_status']}")

cand_ids = []
mc = str(row.get("mapping_candidates","")).strip()
if mc:
    cand_ids = [int(x) for x in mc.split(",") if x.strip().isdigit()]

candidates = []
for rid in cand_ids:
    rule = rule_by_id.get(rid)
    if not rule:
        continue
    hits = matched_keywords_for_rule(rule, desc_lower)
    candidates.append((rid, rule, hits))

if candidates:
    st.write("**Candidates (from mapping_candidates)**")
    for rid, rule, hits in candidates:
        st.write(f"- Rule {rid}: **{rule.budget_category} / {rule.category_detail}** | matched: {', '.join(hits[:6])}{' …' if len(hits)>6 else ''}")
else:
    st.info("No candidate IDs available (NO_MATCH). Use FULL DESCRIPTION keyword or type a keyword.")

suggest_bc = str(row.get("mapped_budget_category","")).strip()
suggest_cd = str(row.get("mapped_category_detail","")).strip()
targets = sorted(set([(rule.budget_category, rule.category_detail) for _, rule, _ in candidates] + ([(suggest_bc, suggest_cd)] if suggest_bc else [])))
if not targets:
    targets = [("UNCATEGORIZED","")]

target = st.selectbox("Approve mapping (category/detail)", options=targets, format_func=lambda x: f"{x[0]} / {x[1]}")

kw_options = ["<use FULL DESCRIPTION (exact)>"] + sorted(set([kw for _,_,hits in candidates for kw in hits]))
kw_choice = st.selectbox("Choose deciding keyword (Option 2)", options=kw_options)
approved_keyword = desc_lower if kw_choice == "<use FULL DESCRIPTION (exact)>" else kw_choice

manual_kw = ""
if not candidates and kw_choice == "<use FULL DESCRIPTION (exact)>":
    manual_kw = st.text_input("Optional: type a shorter keyword (instead of full description)", value="").strip().lower()
    if manual_kw:
        approved_keyword = manual_kw

if st.button("Approve + Save to GitHub (updates data/overrides.csv)", type="primary"):
    ov_text, ov_sha = gh_get_file(OVERRIDES_PATH)
    if not ov_text:
        ov_text = "KEYWORD,BUDGET CATEGORY,CATEGORY DETAIL\n"
        ov_sha = None
    ov_df = pd.read_csv(pd.io.common.StringIO(ov_text))
    ov_df.columns = [c.strip().upper() for c in ov_df.columns]
    if "CATEGORY DETAIL" not in ov_df.columns:
        ov_df["CATEGORY DETAIL"] = ""

    new_row = {"KEYWORD": approved_keyword.lower(), "BUDGET CATEGORY": target[0], "CATEGORY DETAIL": target[1]}
    ov_df = pd.concat([ov_df, pd.DataFrame([new_row])], ignore_index=True)
    ov_df = ov_df.drop_duplicates(subset=["KEYWORD","BUDGET CATEGORY","CATEGORY DETAIL"], keep="last")

    gh_put_file(OVERRIDES_PATH, ov_df.to_csv(index=False), f"Add override: {approved_keyword[:40]}", sha=ov_sha)
    st.success("Saved. Refreshing…")
    st.cache_data.clear()
    st.rerun()
