from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np


KEYWORD_COL_PREFIX = "MERCHANT KEY WORDS"


@dataclass(frozen=True)
class Rule:
    rule_id: int
    budget_category: str
    category_detail: str
    keywords: Tuple[str, ...]  # stored lowercased


def _norm_str(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def load_rules(rules_df: pd.DataFrame) -> List[Rule]:
    """
    Expects columns:
      - BUDGET CATEGORY
      - CATEGORY DETAIL
      - MERCHANT KEY WORDS, MERCHANT KEY WORDS.1, ...
    Keywords are matched as case-insensitive substrings (literal, not regex).
    """
    cols = list(rules_df.columns)
    kw_cols = [c for c in cols if str(c).startswith(KEYWORD_COL_PREFIX)]
    out: List[Rule] = []
    for i, row in rules_df.iterrows():
        bc = _norm_str(row.get("BUDGET CATEGORY"))
        cd = _norm_str(row.get("CATEGORY DETAIL"))
        if not bc:
            continue
        kws = []
        for c in kw_cols:
            k = _norm_str(row.get(c))
            if k:
                kws.append(k.lower())
        if not kws:
            # still allow rule row to exist, but it won't match anything
            kws = []
        out.append(Rule(rule_id=int(i), budget_category=bc, category_detail=cd, keywords=tuple(kws)))
    return out


def load_budgets(budgets_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizes BUDGETS sheet.
    Required columns:
      - BUDGET CATEGORY
      - CATEGORY DETAIL
      - MONTHLY BUDGET
      - ACTIVE (Y/N)
      - NOTES
    """
    df = budgets_df.copy()
    # Normalize column casing/spaces defensively
    df.columns = [str(c).strip() for c in df.columns]
    for col in ["BUDGET CATEGORY", "CATEGORY DETAIL", "ACTIVE (Y/N)", "NOTES"]:
        if col in df.columns:
            df[col] = df[col].astype("object")
    if "ACTIVE (Y/N)" in df.columns:
        df["ACTIVE (Y/N)"] = df["ACTIVE (Y/N)"].apply(lambda x: "Y" if str(x).strip().upper() == "Y" else "")
    df["BUDGET CATEGORY"] = df["BUDGET CATEGORY"].fillna("").astype(str).str.strip()
    if "CATEGORY DETAIL" in df.columns:
        df["CATEGORY DETAIL"] = df["CATEGORY DETAIL"].fillna("").astype(str).str.strip()
    if "MONTHLY BUDGET" in df.columns:
        df["MONTHLY BUDGET"] = pd.to_numeric(df["MONTHLY BUDGET"], errors="coerce").fillna(0.0)
    return df


def load_transactions_any(df: pd.DataFrame, account_fallback: Optional[str] = None) -> pd.DataFrame:
    """
    Accepts a dataframe from:
      - your RAW_TRANSACTIONS_MAPPED sheet (DATE/DESCRIPTION/DT/CR/ACCOUNT/MONTH), or
      - bank extracts tabs (date/description/dt/cr/account)
    Returns normalized columns:
      date, description, debit, credit, account, month, budget_category, category_detail
    """
    d = df.copy()
    d.columns = [str(c).strip() for c in d.columns]

    # Map candidate column names
    def pick(*names):
        for n in names:
            if n in d.columns:
                return n
        return None

    c_date = pick("DATE", "date", "Transaction Date", "Posting Date")
    c_desc = pick("DESCRIPTION", "description", "Details", "Merchant", "Narrative")
    c_dt = pick("DT", "dt", "Debit", "Withdrawal", "Amount Debit")
    c_cr = pick("CR", "cr", "Credit", "Deposit", "Amount Credit")
    c_acct = pick("ACCOUNT", "account")
    c_month = pick("MONTH", "month")
    c_bc = pick("BUDGET CATEGORY", "budget_category")
    c_cd = pick("CATEGORY DETAIL", "category_detail")

    if c_date is None or c_desc is None:
        raise ValueError("Transactions file must have at least date and description columns.")

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(d[c_date], errors="coerce", dayfirst=False)
    out["description"] = d[c_desc].fillna("").astype(str).str.strip()

    out["debit"] = pd.to_numeric(d[c_dt], errors="coerce") if c_dt else np.nan
    out["credit"] = pd.to_numeric(d[c_cr], errors="coerce") if c_cr else np.nan
    out["debit"] = out["debit"].fillna(0.0)
    out["credit"] = out["credit"].fillna(0.0)

    if c_acct:
        out["account"] = d[c_acct].fillna("").astype(str).str.strip()
    else:
        out["account"] = account_fallback or ""

    if c_month:
        out["month"] = d[c_month].fillna("").astype(str).str.strip()
    else:
        out["month"] = out["date"].dt.strftime("%Y-%m")

    out["budget_category"] = d[c_bc].fillna("").astype(str).str.strip() if c_bc else ""
    out["category_detail"] = d[c_cd].fillna("").astype(str).str.strip() if c_cd else ""

    # Drop rows with invalid dates
    out = out[~out["date"].isna()].copy()
    return out


def map_transactions(transactions: pd.DataFrame, rules: List[Rule]) -> pd.DataFrame:
    """
    Deterministic mapping:
      - case-insensitive literal substring match
      - ANY keyword in a rule row triggers that rule
      - 0 matches => UNMAPPED_NO_MATCH
      - 2+ matches => UNMAPPED_MULTI_MATCH
      - 1 match => mapped
    """
    tx = transactions.copy()
    desc = tx["description"].fillna("").astype(str).str.lower()

    # Initialize
    tx["mapping_status"] = ""
    tx["mapping_candidates"] = ""
    tx["mapped_budget_category"] = ""
    tx["mapped_category_detail"] = ""

    # For each tx, gather matching rule ids
    # This is O(N_rules * N_tx). Works well for typical personal-finance sizes.
    candidates: List[List[int]] = [[] for _ in range(len(tx))]

    for rule in rules:
        if not rule.keywords:
            continue
        # Build a boolean mask: any keyword present
        mask = pd.Series(False, index=tx.index)
        for kw in rule.keywords:
            # literal substring search, not regex
            mask = mask | desc.str.contains(kw, regex=False, na=False)
        hit_idx = list(tx.index[mask])
        for ix in hit_idx:
            candidates[tx.index.get_loc(ix)].append(rule.rule_id)

    # Apply mapping decisions
    rule_by_id = {r.rule_id: r for r in rules}
    for pos, ix in enumerate(tx.index):
        hits = candidates[pos]
        if len(hits) == 1:
            r = rule_by_id[hits[0]]
            tx.at[ix, "mapping_status"] = "MAPPED"
            tx.at[ix, "mapped_budget_category"] = r.budget_category
            tx.at[ix, "mapped_category_detail"] = r.category_detail
        elif len(hits) == 0:
            tx.at[ix, "mapping_status"] = "UNMAPPED_NO_MATCH"
            tx.at[ix, "mapping_candidates"] = ""
        else:
            tx.at[ix, "mapping_status"] = "UNMAPPED_MULTI_MATCH"
            tx.at[ix, "mapping_candidates"] = ",".join(map(str, hits))

    # If existing mapping exists, prefer it (so you can pre-fill/override)
    # But only if the row is currently unmapped.
    has_existing = (tx.get("budget_category") is not None) and (tx.get("category_detail") is not None)
    if has_existing:
        existing_bc = tx["budget_category"].fillna("").astype(str).str.strip()
        existing_cd = tx["category_detail"].fillna("").astype(str).str.strip()
        pre_mapped = existing_bc.ne("")
        tx.loc[pre_mapped, "mapped_budget_category"] = existing_bc.loc[pre_mapped]
        tx.loc[pre_mapped, "mapped_category_detail"] = existing_cd.loc[pre_mapped]
        tx.loc[pre_mapped, "mapping_status"] = "MAPPED"

    # Compute net
    tx["net"] = tx["credit"] - tx["debit"]
    return tx


def monthly_rollups(mapped_tx: pd.DataFrame, month: str) -> Dict[str, pd.DataFrame]:
    m = mapped_tx[mapped_tx["month"] == month].copy()
    # Only mapped for the main rollups; unmapped stay in review
    mapped = m[m["mapping_status"] == "MAPPED"].copy()

    by_cat = (
        mapped.groupby(["mapped_budget_category"], dropna=False)[["debit", "credit", "net"]]
        .sum()
        .reset_index()
        .rename(columns={"mapped_budget_category": "BUDGET CATEGORY"})
        .sort_values("BUDGET CATEGORY")
    )

    by_detail = (
        mapped.groupby(["mapped_budget_category", "mapped_category_detail"], dropna=False)[["debit", "credit", "net"]]
        .sum()
        .reset_index()
        .rename(columns={"mapped_budget_category": "BUDGET CATEGORY", "mapped_category_detail": "CATEGORY DETAIL"})
        .sort_values(["BUDGET CATEGORY", "CATEGORY DETAIL"])
    )

    return {"by_category": by_cat, "by_detail": by_detail}


def build_budget_variance(budgets_df: pd.DataFrame, by_category: pd.DataFrame) -> pd.DataFrame:
    """
    Builds a category-level budget vs actual table from ACTIVE(Y/N)=Y rows (parent category budgets).
    Actual Net Spend uses net (credit - debit), so spending is negative.
    Variance = budget_limit - abs(actual_spend) would be one approach, but your Excel uses:
      Variance = Actual Net Spend - (-BudgetLimit)?? 
    For clarity in the app:
      - Budget Limit is positive
      - Actual Spend is positive spending (debit - credit) per category for spend-only categories
    We compute:
      actual_spend = -(net)  (so spend categories become positive)
      variance = budget_limit - actual_spend
      pct = actual_spend / budget_limit  (if budget_limit > 0)
    """
    b = budgets_df.copy()
    # parent budget rows: active Y and blank detail
    parent = b[(b["ACTIVE (Y/N)"] == "Y")].copy()
    parent = parent.rename(columns={"MONTHLY BUDGET": "BUDGET LIMIT"})
    parent["CATEGORY DETAIL"] = ""

    actual = by_category.copy()
    actual["actual_spend"] = (-actual["net"]).clip(lower=0)  # spending as positive
    actual = actual[["BUDGET CATEGORY", "actual_spend"]]

    out = parent.merge(actual, on="BUDGET CATEGORY", how="left")
    out["actual_spend"] = out["actual_spend"].fillna(0.0)
    out["Variance"] = out["BUDGET LIMIT"] - out["actual_spend"]
    out["%"] = np.where(out["BUDGET LIMIT"] > 0, out["actual_spend"] / out["BUDGET LIMIT"], np.nan)

    # Reason/Notes fields for UI tagging
    out["Reason"] = ""
    out["Notes"] = ""

    cols = ["BUDGET CATEGORY", "CATEGORY DETAIL", "BUDGET LIMIT", "actual_spend", "Variance", "%", "Reason", "Notes"]
    out = out[cols].sort_values("BUDGET CATEGORY")
    out = out.rename(columns={"actual_spend": "Actual Spend"})
    return out


def build_summary(mapped_tx: pd.DataFrame, month: str, income_categories: List[str], transfer_categories: List[str]) -> Dict[str, float]:
    """
    Computes:
      - total_income: sum of credits in income categories
      - total_spending: sum of debits minus credits in spend categories (positive)
      - discretionary: income - spending
    transfer categories are excluded from both income and spending totals
    """
    m = mapped_tx[(mapped_tx["month"] == month) & (mapped_tx["mapping_status"] == "MAPPED")].copy()
    cat = m["mapped_budget_category"].fillna("").astype(str)

    is_transfer = cat.isin(set(transfer_categories))
    is_income = cat.isin(set(income_categories)) & (~is_transfer)
    is_spend = (~cat.isin(set(income_categories))) & (~is_transfer)

    total_income = m.loc[is_income, "credit"].sum()
    # spending as positive: debit - credit (handles refunds in spend categories)
    total_spending = (m.loc[is_spend, "debit"].sum() - m.loc[is_spend, "credit"].sum())
    discretionary = total_income - total_spending

    return {
        "TOTAL INCOME": float(total_income),
        "TOTAL SPENDING": float(total_spending),
        "DISCRETIONARY": float(discretionary),
    }
