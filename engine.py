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
    keywords: Tuple[str, ...]  # lowercased


@dataclass(frozen=True)
class OverrideRule:
    keyword: str  # lowercased
    budget_category: str
    category_detail: str


def load_rules_from_excel(rules_df: pd.DataFrame) -> List[Rule]:
    cols = list(rules_df.columns)
    kw_cols = [c for c in cols if str(c).startswith(KEYWORD_COL_PREFIX)]
    out: List[Rule] = []
    for i, row in rules_df.iterrows():
        bc = str(row.get("BUDGET CATEGORY", "")).strip()
        cd = str(row.get("CATEGORY DETAIL", "")).strip()
        if not bc:
            continue
        kws = []
        for c in kw_cols:
            k = str(row.get(c, "")).strip()
            if k and k.lower() != "nan":
                kws.append(k.lower())
        out.append(Rule(rule_id=int(i), budget_category=bc, category_detail=cd, keywords=tuple(kws)))
    return out


def rules_to_dataframe(rules: List[Rule]) -> pd.DataFrame:
    max_kw = max((len(r.keywords) for r in rules), default=0)
    rows = []
    for r in rules:
        row = {"RULE_ID": r.rule_id, "BUDGET CATEGORY": r.budget_category, "CATEGORY DETAIL": r.category_detail}
        for j in range(max_kw):
            col = KEYWORD_COL_PREFIX if j == 0 else f"{KEYWORD_COL_PREFIX}.{j}"
            row[col] = r.keywords[j] if j < len(r.keywords) else ""
        rows.append(row)
    return pd.DataFrame(rows)


def load_rules_from_csv(csv_text: str) -> List[Rule]:
    df = pd.read_csv(pd.io.common.StringIO(csv_text))
    kw_cols = [c for c in df.columns if str(c).startswith(KEYWORD_COL_PREFIX)]
    out: List[Rule] = []
    for idx, row in df.iterrows():
        rid = int(row["RULE_ID"]) if "RULE_ID" in df.columns else int(idx)
        bc = str(row.get("BUDGET CATEGORY", "")).strip()
        cd = str(row.get("CATEGORY DETAIL", "")).strip()
        if not bc:
            continue
        kws = []
        for c in kw_cols:
            k = str(row.get(c, "")).strip()
            if k and k.lower() != "nan":
                kws.append(k.lower())
        out.append(Rule(rule_id=rid, budget_category=bc, category_detail=cd, keywords=tuple(kws)))
    return out


def load_budgets_from_excel(budgets_df: pd.DataFrame) -> pd.DataFrame:
    df = budgets_df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "ACTIVE (Y/N)" in df.columns:
        df["ACTIVE (Y/N)"] = df["ACTIVE (Y/N)"].apply(lambda x: "Y" if str(x).strip().upper() == "Y" else "")
    for c in ["BUDGET CATEGORY", "CATEGORY DETAIL", "NOTES"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
    if "MONTHLY BUDGET" in df.columns:
        df["MONTHLY BUDGET"] = pd.to_numeric(df["MONTHLY BUDGET"], errors="coerce").fillna(0.0)
    return df


def load_budgets_from_csv(csv_text: str) -> pd.DataFrame:
    df = pd.read_csv(pd.io.common.StringIO(csv_text))
    df.columns = [str(c).strip() for c in df.columns]
    if "ACTIVE (Y/N)" in df.columns:
        df["ACTIVE (Y/N)"] = df["ACTIVE (Y/N)"].apply(lambda x: "Y" if str(x).strip().upper() == "Y" else "")
    for c in ["BUDGET CATEGORY", "CATEGORY DETAIL", "NOTES"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()
    if "MONTHLY BUDGET" in df.columns:
        df["MONTHLY BUDGET"] = pd.to_numeric(df["MONTHLY BUDGET"], errors="coerce").fillna(0.0)
    return df


def load_overrides_from_csv(csv_text: str) -> List[OverrideRule]:
    df = pd.read_csv(pd.io.common.StringIO(csv_text))
    df.columns = [str(c).strip().upper() for c in df.columns]
    out: List[OverrideRule] = []
    if "KEYWORD" not in df.columns or "BUDGET CATEGORY" not in df.columns:
        return out
    if "CATEGORY DETAIL" not in df.columns:
        df["CATEGORY DETAIL"] = ""
    for _, row in df.iterrows():
        kw = str(row.get("KEYWORD", "")).strip()
        bc = str(row.get("BUDGET CATEGORY", "")).strip()
        cd = str(row.get("CATEGORY DETAIL", "")).strip()
        if kw and bc:
            out.append(OverrideRule(keyword=kw.lower(), budget_category=bc, category_detail=cd))
    return out


def load_transactions_any(df: pd.DataFrame, account_fallback: Optional[str] = None) -> pd.DataFrame:
    d = df.copy()
    d.columns = [str(c).strip() for c in d.columns]

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

    if c_date is None or c_desc is None:
        raise ValueError("Transactions file must have at least date and description columns.")

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(d[c_date], errors="coerce", dayfirst=False)
    out["description"] = d[c_desc].fillna("").astype(str).str.strip()
    out["debit"] = pd.to_numeric(d[c_dt], errors="coerce") if c_dt else np.nan
    out["credit"] = pd.to_numeric(d[c_cr], errors="coerce") if c_cr else np.nan
    out["debit"] = out["debit"].fillna(0.0)
    out["credit"] = out["credit"].fillna(0.0)

    out["account"] = d[c_acct].fillna("").astype(str).str.strip() if c_acct else (account_fallback or "")
    out["month"] = d[c_month].fillna("").astype(str).str.strip() if c_month else out["date"].dt.strftime("%Y-%m")
    out = out[~out["date"].isna()].copy()
    return out


def map_transactions(transactions: pd.DataFrame, rules: List[Rule], overrides: Optional[List[OverrideRule]] = None) -> pd.DataFrame:
    tx = transactions.copy()
    desc = tx["description"].fillna("").astype(str).str.lower()

    tx["mapping_status"] = ""
    tx["mapping_candidates"] = ""
    tx["mapped_budget_category"] = ""
    tx["mapped_category_detail"] = ""
    tx["override_status"] = ""

    # Overrides first
    overrides = overrides or []
    if overrides:
        hits_by_row: List[List[OverrideRule]] = [[] for _ in range(len(tx))]
        for ov in overrides:
            mask = desc.str.contains(ov.keyword, regex=False, na=False)
            for ix in tx.index[mask]:
                hits_by_row[tx.index.get_loc(ix)].append(ov)

        for pos, ix in enumerate(tx.index):
            hits = hits_by_row[pos]
            if not hits:
                continue
            targets = {(h.budget_category, h.category_detail) for h in hits}
            if len(targets) == 1:
                bc, cd = next(iter(targets))
                tx.at[ix, "override_status"] = "OVERRIDE"
                tx.at[ix, "mapping_status"] = "MAPPED"
                tx.at[ix, "mapped_budget_category"] = bc
                tx.at[ix, "mapped_category_detail"] = cd
            else:
                tx.at[ix, "override_status"] = "OVERRIDE_CONFLICT"
                tx.at[ix, "mapping_status"] = "UNMAPPED_MULTI_MATCH"

    # Candidates
    candidates: List[List[int]] = [[] for _ in range(len(tx))]
    for rule in rules:
        if not rule.keywords:
            continue
        mask = pd.Series(False, index=tx.index)
        for kw in rule.keywords:
            mask = mask | desc.str.contains(kw, regex=False, na=False)
        mask = mask & (tx["mapping_status"] == "")
        for ix in tx.index[mask]:
            candidates[tx.index.get_loc(ix)].append(rule.rule_id)

    rule_by_id = {r.rule_id: r for r in rules}

    def best_rule_by_specificity(hits: List[int], d: str) -> Optional[int]:
        best = None
        best_score = -1
        tie = False
        for rid in hits:
            r = rule_by_id[rid]
            score = 0
            for kw in r.keywords:
                if kw and (kw in d):
                    score = max(score, len(kw))
            if score > best_score:
                best_score = score
                best = rid
                tie = False
            elif score == best_score:
                tie = True
        if best is None or tie:
            return None
        return best

    for pos, ix in enumerate(tx.index):
        if tx.at[ix, "mapping_status"] != "":
            continue
        hits = candidates[pos]
        if not hits:
            tx.at[ix, "mapping_status"] = "UNMAPPED_NO_MATCH"
            continue

        targets = {(rule_by_id[rid].budget_category, rule_by_id[rid].category_detail) for rid in hits}
        if len(targets) == 1:
            bc, cd = next(iter(targets))
            tx.at[ix, "mapping_status"] = "MAPPED"
            tx.at[ix, "mapped_budget_category"] = bc
            tx.at[ix, "mapped_category_detail"] = cd
            continue

        d = str(tx.at[ix, "description"]).lower()
        best = best_rule_by_specificity(hits, d)
        tx.at[ix, "mapping_candidates"] = ",".join(map(str, hits))
        if best is not None:
            r = rule_by_id[best]
            tx.at[ix, "mapping_status"] = "REVIEW_PICKED"
            tx.at[ix, "mapped_budget_category"] = r.budget_category
            tx.at[ix, "mapped_category_detail"] = r.category_detail
        else:
            tx.at[ix, "mapping_status"] = "UNMAPPED_MULTI_MATCH"

    tx["net"] = tx["credit"] - tx["debit"]
    return tx


def monthly_rollups(mapped_tx: pd.DataFrame, month: str) -> Dict[str, pd.DataFrame]:
    m = mapped_tx[mapped_tx["month"] == month].copy()
    mapped = m[m["mapping_status"].isin(["MAPPED", "REVIEW_PICKED"])].copy()

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
    b = budgets_df.copy()
    if "ACTIVE (Y/N)" not in b.columns:
        b["ACTIVE (Y/N)"] = ""
    parent = b[b["ACTIVE (Y/N)"] == "Y"].copy()
    parent = parent.rename(columns={"MONTHLY BUDGET": "BUDGET LIMIT"})
    parent["CATEGORY DETAIL"] = ""

    actual = by_category.copy()
    actual["Actual Spend"] = (-actual["net"]).clip(lower=0)
    actual = actual[["BUDGET CATEGORY", "Actual Spend"]]

    out = parent.merge(actual, on="BUDGET CATEGORY", how="left")
    out["Actual Spend"] = out["Actual Spend"].fillna(0.0)
    out["Variance"] = out["BUDGET LIMIT"] - out["Actual Spend"]
    out["%"] = np.where(out["BUDGET LIMIT"] > 0, out["Actual Spend"] / out["BUDGET LIMIT"], np.nan)

    out["Reason"] = ""
    out["Notes"] = ""
    cols = ["BUDGET CATEGORY", "CATEGORY DETAIL", "BUDGET LIMIT", "Actual Spend", "Variance", "%", "Reason", "Notes"]
    return out[cols].sort_values("BUDGET CATEGORY")


def build_summary(mapped_tx: pd.DataFrame, month: str, income_categories: List[str], transfer_categories: List[str]) -> Dict[str, float]:
    m = mapped_tx[(mapped_tx["month"] == month) & (mapped_tx["mapping_status"].isin(["MAPPED","REVIEW_PICKED"]))].copy()
    cat = m["mapped_budget_category"].fillna("").astype(str)

    is_transfer = cat.isin(set(transfer_categories))
    is_income = cat.isin(set(income_categories)) & (~is_transfer)
    is_spend = (~cat.isin(set(income_categories))) & (~is_transfer)

    total_income = m.loc[is_income, "credit"].sum()
    total_spending = (m.loc[is_spend, "debit"].sum() - m.loc[is_spend, "credit"].sum())
    discretionary = total_income - total_spending
    return {"TOTAL INCOME": float(total_income), "TOTAL SPENDING": float(total_spending), "DISCRETIONARY": float(discretionary)}


def matched_keywords_for_rule(rule: Rule, description_lower: str) -> List[str]:
    return [kw for kw in rule.keywords if kw and kw in description_lower]
