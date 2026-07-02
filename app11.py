"""
Munshi — Your AI Business Accountant (for Indian MSMEs)
=======================================================
Dependency-light build: needs only `streamlit` and `pandas`.
Charts use Streamlit's built-in charting; the optional AI narrative uses the
Python standard library (urllib) — so there is nothing extra to install.

Numbers are ALWAYS computed in Python. The optional LLM only *explains* them.
Munshi is a decision-support tool, not a substitute for a CA.

Deploy: push to a public GitHub repo, point share.streamlit.io at this file.
"""

from __future__ import annotations

import re
import json
import urllib.request
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# ----------------------------------------------------------------------------
# Constants (the law)
# ----------------------------------------------------------------------------
STATUTORY_DAYS_WITH_AGREEMENT = 45   # MSMED Act cap with a written agreement
STATUTORY_DAYS_NO_AGREEMENT = 15     # default with no written agreement
DEFAULT_RBI_BANK_RATE = 6.75         # % p.a.; delay interest = 3x this, compounded
DEFAULT_CREDIT_DAYS = 30             # used when the user is NOT a registered micro/small unit

# Palette — "ink & brass on ledger paper"
INK = "#1B1F3B"; INK_SOFT = "#3A3F63"
BRASS = "#C08A2D"; PAPER = "#FBF7EF"; PAPER_LINE = "#E9E1D2"
GOOD = "#2E7D5B"; WARN = "#B8791F"; ALERT = "#B23A3A"; MUTED = "#6B6552"

st.set_page_config(page_title="Munshi — AI Business Accountant",
                   page_icon="📒", layout="wide")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap');
.stApp {{ background:{PAPER}; }}
.block-container {{ padding-top:2.2rem; max-width:1180px; }}
h1,h2,h3,h4 {{ font-family:'Fraunces',Georgia,serif !important; color:{INK}; letter-spacing:-.01em; }}
html,body,[class*="css"],p,div,span,label,.stMarkdown {{ font-family:'Inter',system-ui,sans-serif; color:{INK}; }}
.brand {{ font-family:'Fraunces',serif; font-weight:700; font-size:1.9rem; color:{INK}; line-height:1; }}
.brand .dot {{ color:{BRASS}; }}
.brand-sub {{ font-size:.72rem; letter-spacing:.18em; text-transform:uppercase; color:{MUTED}; margin-top:.35rem; }}
.kpi {{ background:#fff; border:1px solid {PAPER_LINE}; border-top:3px solid {BRASS};
        border-radius:10px; padding:1.05rem 1.1rem .95rem; box-shadow:0 1px 2px rgba(27,31,59,.04); height:100%; }}
.kpi .lbl {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.09em; color:{MUTED}; font-weight:600; }}
.kpi .val {{ font-family:'Fraunces',serif; font-size:1.72rem; font-weight:600; margin-top:.25rem; color:{INK}; }}
.kpi .sub {{ font-size:.8rem; color:{MUTED}; margin-top:.15rem; }}
.kpi.good .val {{ color:{GOOD}; }} .kpi.alert .val {{ color:{ALERT}; }} .kpi.warn .val {{ color:{WARN}; }}
.eyebrow {{ font-size:.74rem; text-transform:uppercase; letter-spacing:.14em; color:{BRASS}; font-weight:700; margin-bottom:.15rem; }}
.munshi {{ background:#fff; border:1px solid {PAPER_LINE}; border-left:4px solid {BRASS};
           border-radius:10px; padding:1.1rem 1.25rem; margin:.4rem 0; }}
.chip {{ display:inline-block; font-size:.72rem; font-weight:600; padding:.12rem .55rem; border-radius:999px; margin-right:.4rem; }}
.chip.good {{ background:#E3F1EA; color:{GOOD}; }} .chip.warn {{ background:#F6ECD6; color:{WARN}; }}
.chip.alert {{ background:#F6E1E1; color:{ALERT}; }} .chip.info {{ background:#E7E9F2; color:{INK_SOFT}; }}
.chip.action {{ background:{INK}; color:#fff; }}
section[data-testid="stSidebar"] {{ background:#fff; border-right:1px solid {PAPER_LINE}; }}
.stButton>button {{ border-radius:8px; border:1px solid {INK}; background:{INK}; color:#fff; font-weight:600; }}
.stButton>button:hover {{ background:{INK_SOFT}; border-color:{INK_SOFT}; color:#fff; }}
hr {{ border-color:{PAPER_LINE}; }} .small {{ font-size:.8rem; color:{MUTED}; }}
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def format_inr(n) -> str:
    try:
        n = round(float(n))
    except (TypeError, ValueError):
        return "₹0"
    neg = n < 0
    s = str(abs(n))
    if len(s) > 3:
        last3, rest = s[-3:], s[:-3]
        rest = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", rest)
        s = rest + "," + last3
    return ("-" if neg else "") + "₹" + s


def kpi(col, label, value, sub="", tone=""):
    col.markdown(f"<div class='kpi {tone}'><div class='lbl'>{label}</div>"
                 f"<div class='val'>{value}</div><div class='sub'>{sub}</div></div>",
                 unsafe_allow_html=True)


def statutory_days(has_agreement: bool, user_is_msme: bool, credit_days: int) -> int:
    if not user_is_msme:
        return credit_days
    return STATUTORY_DAYS_WITH_AGREEMENT if has_agreement else STATUTORY_DAYS_NO_AGREEMENT


def delay_interest(amount: float, days_late: int, rbi_rate: float) -> float:
    if days_late <= 0:
        return 0.0
    monthly = (3 * rbi_rate / 100) / 12
    return amount * ((1 + monthly) ** (days_late / 30.0) - 1)


def enrich_invoices(df, today, user_is_msme, credit_days, rbi_rate):
    df = df.copy()
    if df.empty:
        for c in ["due_date", "status", "days_overdue", "interest_owed", "outstanding"]:
            df[c] = pd.Series(dtype="object")
        return df
    due, status, overdue, interest, outstanding = [], [], [], [], []
    for _, r in df.iterrows():
        inv_d = pd.to_datetime(r["invoice_date"]).date()
        d = statutory_days(bool(r["has_agreement"]), user_is_msme, credit_days)
        dd = inv_d + timedelta(days=d)
        due.append(dd)
        if bool(r["paid"]):
            paid_d = pd.to_datetime(r["paid_date"]).date() if pd.notna(r["paid_date"]) else today
            late = (paid_d - dd).days
            status.append("Paid (late)" if late > 0 else "Paid")
            overdue.append(max(late, 0)); interest.append(delay_interest(r["amount"], max(late, 0), rbi_rate))
            outstanding.append(0.0)
        else:
            late = (today - dd).days
            status.append("Overdue" if late > 0 else ("Due soon" if (dd - today).days <= 7 else "On track"))
            overdue.append(max(late, 0)); interest.append(delay_interest(r["amount"], max(late, 0), rbi_rate))
            outstanding.append(float(r["amount"]))
    df["due_date"] = due; df["status"] = status; df["days_overdue"] = overdue
    df["interest_owed"] = interest; df["outstanding"] = outstanding
    return df


def enrich_expenses(df, today):
    df = df.copy()
    if df.empty:
        for c in ["due_date", "compliance"]:
            df[c] = pd.Series(dtype="object")
        return df
    due, comp = [], []
    for _, r in df.iterrows():
        exp_d = pd.to_datetime(r["expense_date"]).date()
        if bool(r["vendor_is_msme"]) and not bool(r["paid"]):
            dd = exp_d + timedelta(days=STATUTORY_DAYS_WITH_AGREEMENT)
            due.append(dd); left = (dd - today).days
            comp.append("Deduction at risk" if left < 0 else ("Pay soon" if left <= 10 else "OK"))
        else:
            due.append(None); comp.append("—")
    df["due_date"] = due; df["compliance"] = comp
    return df


def build_summary(inv, exp):
    revenue = float(inv["amount"].sum()) if not inv.empty else 0.0
    expenses = float(exp["amount"].sum()) if not exp.empty else 0.0
    net = revenue - expenses
    cash_in = float(inv.loc[inv["paid"], "amount"].sum()) if not inv.empty else 0.0
    cash_out = float(exp.loc[exp["paid"], "amount"].sum()) if not exp.empty else 0.0
    outstanding = float(inv["outstanding"].sum()) if not inv.empty else 0.0
    overdue_amt = float(inv.loc[inv["status"] == "Overdue", "outstanding"].sum()) if not inv.empty else 0.0
    interest_owed = float(inv.loc[~inv["paid"], "interest_owed"].sum()) if not inv.empty else 0.0
    at_risk = exp[exp.get("compliance") == "Deduction at risk"] if "compliance" in exp else pd.DataFrame()
    payable_risk = float(at_risk["amount"].sum()) if not at_risk.empty else 0.0
    return dict(revenue=revenue, expenses=expenses, net=net,
                margin=(net / revenue * 100 if revenue else 0),
                cash_in=cash_in, cash_out=cash_out, net_cash=cash_in - cash_out,
                outstanding=outstanding, overdue_amt=overdue_amt,
                interest_owed=interest_owed, payable_risk=payable_risk)


def generate_insights(inv, exp, s):
    out = []
    if s["net"] >= 0:
        out.append(("good", f"Your business is profitable: a net surplus of {format_inr(s['net'])} "
                    f"at a {s['margin']:.0f}% margin on {format_inr(s['revenue'])} of billings."))
    else:
        out.append(("alert", f"You're running a loss of {format_inr(-s['net'])}. Expenses "
                    f"({format_inr(s['expenses'])}) are outpacing billings ({format_inr(s['revenue'])})."))
    if s["net_cash"] >= 0:
        out.append(("info", f"Cash collected exceeds cash paid out by {format_inr(s['net_cash'])} — but "
                    f"{format_inr(s['outstanding'])} is still stuck in unpaid invoices."))
    else:
        out.append(("warn", f"More cash has left ({format_inr(s['cash_out'])}) than has come in "
                    f"({format_inr(s['cash_in'])}). Collecting receivables is your fastest fix."))
    if not inv.empty:
        od = inv[inv["status"] == "Overdue"].sort_values("outstanding", ascending=False)
        if not od.empty:
            names = ", ".join(od["buyer"].head(3).tolist())
            out.append(("alert", f"{len(od)} invoice(s) worth {format_inr(s['overdue_amt'])} are past "
                        f"the statutory deadline. Biggest: {names}."))
            if s["interest_owed"] > 0:
                out.append(("action", f"Under the MSMED Act these late buyers legally owe you "
                            f"~{format_inr(s['interest_owed'])} in interest. A reminder that names the "
                            f"amount moves payment faster than a polite nudge."))
        else:
            out.append(("good", "No invoice has crossed its 15/45-day deadline yet. Keep it that way."))
        if not od.empty:
            out.append(("action", f"This week: chase {od.iloc[0]['buyer']} for "
                        f"{format_inr(od.iloc[0]['outstanding'])} first — your largest overdue amount."))
    if s["payable_risk"] > 0:
        out.append(("alert", f"You owe {format_inr(s['payable_risk'])} to MSME vendors past 45 days. "
                    f"Clear these before year-end or the expense is disallowed and your tax bill rises."))
    return out


def summary_text_for_llm(inv, exp, s):
    lines = [
        f"Billings (revenue): {format_inr(s['revenue'])}",
        f"Expenses: {format_inr(s['expenses'])}",
        f"Net profit: {format_inr(s['net'])} ({s['margin']:.0f}% margin)",
        f"Cash collected: {format_inr(s['cash_in'])}; cash paid out: {format_inr(s['cash_out'])}",
        f"Outstanding receivables: {format_inr(s['outstanding'])} (overdue {format_inr(s['overdue_amt'])})",
        f"Interest legally owed to you by late buyers: {format_inr(s['interest_owed'])}",
        f"MSME payables past 45 days (tax-deduction risk): {format_inr(s['payable_risk'])}",
    ]
    if not inv.empty:
        od = inv[inv["status"] == "Overdue"].sort_values("outstanding", ascending=False).head(5)
        if not od.empty:
            lines.append("Top overdue buyers: " + "; ".join(
                f"{r['buyer']} {format_inr(r['outstanding'])} ({int(r['days_overdue'])}d late)"
                for _, r in od.iterrows()))
    return "\n".join(lines)


def get_ai_briefing(api_key, model, summary):
    """Optional AI narrative using only the standard library (no 'requests' needed)."""
    prompt = ("You are Munshi, a warm but honest accountant for an Indian small-business owner. "
              "Below are ALREADY-COMPUTED figures. Do NOT recompute or invent any numbers; only "
              "reference the ones given. Write a short briefing (5-7 sentences) explaining what's "
              "happening, flag the single most important risk, and end with 2-3 concrete next actions. "
              "Plain English, Indian context.\n\nFIGURES:\n" + summary)
    body = json.dumps({"model": model, "max_tokens": 700,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


# ----------------------------------------------------------------------------
# Sample data (relative to today, so the demo always looks live)
# ----------------------------------------------------------------------------
def sample_invoices(today):
    rows = [("Croma Retail Ltd", 58, 185000, True, False, None),
            ("Sunrise Textiles", 22, 92000, True, True, 20),
            ("Apex Engineering", 70, 240000, False, False, None),
            ("Kirana Mart", 10, 34000, False, False, None),
            ("Bluewave Exports", 52, 128000, True, True, 50),
            ("Metro Interiors", 38, 76000, False, False, None),
            ("Reliance Digital", 95, 310000, True, False, None)]
    return pd.DataFrame([dict(id=i + 1, buyer=b, invoice_date=today - timedelta(days=age),
                              amount=amt, has_agreement=agr, paid=paid,
                              paid_date=(today - timedelta(days=po)) if po else pd.NaT)
                         for i, (b, age, amt, agr, paid, po) in enumerate(rows)])


def sample_expenses(today):
    rows = [("Shree Raw Materials", "Raw material", 40, 120000, True, False),
            ("Landlord", "Rent", 12, 45000, False, True),
            ("Team payroll", "Salaries", 8, 165000, False, True),
            ("PowerGrid", "Utilities", 15, 18000, False, True),
            ("CleverBooks SaaS", "Software", 20, 2400, False, True),
            ("Metal Craft Co", "Raw material", 55, 88000, True, False)]
    return pd.DataFrame([dict(id=i + 1, vendor=v, category=c, expense_date=today - timedelta(days=age),
                              amount=amt, vendor_is_msme=msme, paid=paid,
                              paid_date=(today - timedelta(days=age)) if paid else pd.NaT)
                         for i, (v, c, age, amt, msme, paid) in enumerate(rows)])


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------
TODAY = date.today()
st.session_state.setdefault("invoices", sample_invoices(TODAY))
st.session_state.setdefault("expenses", sample_expenses(TODAY))
st.session_state.setdefault("user_is_msme", True)
st.session_state.setdefault("rbi_rate", DEFAULT_RBI_BANK_RATE)
st.session_state.setdefault("credit_days", DEFAULT_CREDIT_DAYS)

# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("<div class='brand'>Munshi<span class='dot'>.</span></div>"
                "<div class='brand-sub'>Your AI business accountant</div>", unsafe_allow_html=True)
    st.write("")
    page = st.radio("Go to", ["Dashboard", "Receivables", "Expenses",
                              "Financial statements", "Ask Munshi"], label_visibility="collapsed")
    st.divider()
    with st.expander("Business settings"):
        st.session_state.user_is_msme = st.toggle("I'm a registered Micro/Small unit",
                                                  value=st.session_state.user_is_msme,
                                                  help="Turns on the 15/45-day clock on receivables.")
        st.session_state.rbi_rate = st.number_input("RBI bank rate (% p.a.)", 1.0, 20.0,
                                                    st.session_state.rbi_rate, 0.25,
                                                    help="Delay interest is 3x this, compounded monthly.")
        if not st.session_state.user_is_msme:
            st.session_state.credit_days = st.number_input("Default credit period (days)", 7, 120,
                                                          st.session_state.credit_days, 1)
    with st.expander("Data"):
        up = st.file_uploader("Import invoices CSV", type="csv", key="inv_up")
        if up is not None:
            try:
                st.session_state.invoices = pd.read_csv(up, parse_dates=["invoice_date", "paid_date"])
                st.success("Invoices imported.")
            except Exception as e:
                st.error(f"Could not read that file: {e}")
        st.download_button("Export invoices CSV",
                           st.session_state.invoices.to_csv(index=False).encode(),
                           "munshi_invoices.csv", "text/csv")
        if st.button("Reset to sample data"):
            st.session_state.invoices = sample_invoices(TODAY)
            st.session_state.expenses = sample_expenses(TODAY)
            st.rerun()
    st.divider()
    st.markdown("<div class='small'>Numbers are computed in Python. Munshi's AI only explains "
                "them — it never does the math. Not a substitute for a CA.</div>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# Compute
# ----------------------------------------------------------------------------
inv = enrich_invoices(st.session_state.invoices, TODAY, st.session_state.user_is_msme,
                      st.session_state.credit_days, st.session_state.rbi_rate)
exp = enrich_expenses(st.session_state.expenses, TODAY)
S = build_summary(inv, exp)


def status_color(v):
    return {"Overdue": f"color:{ALERT};font-weight:600", "Due soon": f"color:{WARN};font-weight:600",
            "On track": f"color:{GOOD}", "Paid": f"color:{MUTED}",
            "Paid (late)": f"color:{WARN}"}.get(v, "")


# ----------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------
def page_dashboard():
    st.markdown("<div class='eyebrow'>Overview</div>", unsafe_allow_html=True)
    st.markdown("# The state of your business, today")
    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "Net profit", format_inr(S["net"]), f"{S['margin']:.0f}% margin",
        "good" if S["net"] >= 0 else "alert")
    kpi(c2, "Cash in hand (net)", format_inr(S["net_cash"]), "collected − paid out",
        "good" if S["net_cash"] >= 0 else "warn")
    kpi(c3, "Money owed to you", format_inr(S["outstanding"]),
        f"{format_inr(S['overdue_amt'])} overdue", "alert" if S["overdue_amt"] > 0 else "")
    kpi(c4, "Interest owed to you", format_inr(S["interest_owed"]),
        "by late buyers (MSMED Act)", "warn" if S["interest_owed"] > 0 else "")

    st.write("")
    left, right = st.columns(2)
    with left:
        st.markdown("#### Money in vs money out")
        st.bar_chart(pd.DataFrame({"₹": [S["revenue"], S["cash_in"], S["expenses"], S["cash_out"]]},
                                  index=["Billed", "Collected", "Expenses", "Paid out"]),
                     color=BRASS, height=300)
    with right:
        st.markdown("#### Receivables by age")
        buckets = {"Not due": 0.0, "1–15 late": 0.0, "16–45 late": 0.0, "45+ late": 0.0}
        if not inv.empty:
            for _, r in inv[~inv["paid"]].iterrows():
                d = int(r["days_overdue"])
                key = ("Not due" if d <= 0 else "1–15 late" if d <= 15
                       else "16–45 late" if d <= 45 else "45+ late")
                buckets[key] += r["outstanding"]
        st.bar_chart(pd.DataFrame({"₹": list(buckets.values())}, index=list(buckets.keys())),
                     color=ALERT, height=300)

    st.markdown("#### Munshi's quick read")
    for level, text in generate_insights(inv, exp, S)[:3]:
        st.markdown(f"<div class='munshi'><span class='chip {level}'>{level.upper()}</span>{text}</div>",
                    unsafe_allow_html=True)


def page_receivables():
    st.markdown("<div class='eyebrow'>Receivables</div>", unsafe_allow_html=True)
    st.markdown("# Who owes you — and when it's legally overdue")
    with st.expander("➕ Add an invoice"):
        c = st.columns(4)
        buyer = c[0].text_input("Buyer")
        amount = c[1].number_input("Amount (₹)", 0, step=1000)
        inv_date = c[2].date_input("Invoice date", TODAY)
        agr = c[3].toggle("Written agreement?", value=True)
        paid = c[0].toggle("Already paid?", value=False)
        paid_date = c[1].date_input("Paid on", TODAY) if paid else None
        if st.button("Save invoice"):
            if buyer and amount:
                new = dict(id=int(st.session_state.invoices["id"].max() or 0) + 1, buyer=buyer,
                           invoice_date=pd.Timestamp(inv_date), amount=amount, has_agreement=agr,
                           paid=paid, paid_date=pd.Timestamp(paid_date) if paid_date else pd.NaT)
                st.session_state.invoices = pd.concat(
                    [st.session_state.invoices, pd.DataFrame([new])], ignore_index=True)
                st.rerun()
            else:
                st.warning("Add at least a buyer and an amount.")

    show = inv.copy()
    show["Amount"] = show["amount"].map(format_inr)
    show["Interest owed"] = show["interest_owed"].map(lambda x: format_inr(x) if x else "—")
    show = show.rename(columns={"buyer": "Buyer", "invoice_date": "Invoiced", "due_date": "Due by",
                                "status": "Status", "days_overdue": "Days late"})
    view = show[["Buyer", "Invoiced", "Due by", "Amount", "Status", "Days late", "Interest owed"]]
    st.dataframe(view.style.map(status_color, subset=["Status"]),
                 use_container_width=True, hide_index=True)
    st.markdown(f"<div class='small'>Total outstanding <b>{format_inr(S['outstanding'])}</b> · "
                f"overdue <b>{format_inr(S['overdue_amt'])}</b> · interest legally owed to you "
                f"<b>{format_inr(S['interest_owed'])}</b></div>", unsafe_allow_html=True)


def page_expenses():
    st.markdown("<div class='eyebrow'>Expenses & payables</div>", unsafe_allow_html=True)
    st.markdown("# What you spend — and MSME dues to clear in time")
    with st.expander("➕ Add an expense"):
        c = st.columns(4)
        vendor = c[0].text_input("Vendor")
        cat = c[1].text_input("Category", "Raw material")
        amt = c[2].number_input("Amount (₹)", 0, step=500)
        exp_date = c[3].date_input("Date", TODAY)
        msme = c[0].toggle("Vendor is an MSME?", value=False)
        paid = c[1].toggle("Paid?", value=True)
        if st.button("Save expense"):
            if vendor and amt:
                new = dict(id=int(st.session_state.expenses["id"].max() or 0) + 1, vendor=vendor,
                           category=cat, expense_date=pd.Timestamp(exp_date), amount=amt,
                           vendor_is_msme=msme, paid=paid,
                           paid_date=pd.Timestamp(exp_date) if paid else pd.NaT)
                st.session_state.expenses = pd.concat(
                    [st.session_state.expenses, pd.DataFrame([new])], ignore_index=True)
                st.rerun()
            else:
                st.warning("Add at least a vendor and an amount.")

    show = exp.copy()
    show["Amount"] = show["amount"].map(format_inr)
    show = show.rename(columns={"vendor": "Vendor", "category": "Category", "expense_date": "Date",
                                "due_date": "MSME due by", "compliance": "43B(h) status", "paid": "Paid"})
    view = show[["Vendor", "Category", "Date", "Amount", "Paid", "MSME due by", "43B(h) status"]]

    def comp_color(v):
        return {"Deduction at risk": f"color:{ALERT};font-weight:600",
                "Pay soon": f"color:{WARN};font-weight:600", "OK": f"color:{GOOD}"}.get(v, f"color:{MUTED}")

    st.dataframe(view.style.map(comp_color, subset=["43B(h) status"]),
                 use_container_width=True, hide_index=True)
    if S["payable_risk"] > 0:
        st.markdown(f"<div class='munshi'><span class='chip alert'>ALERT</span>"
                    f"{format_inr(S['payable_risk'])} owed to MSME vendors is past 45 days — clear it "
                    f"before year-end or lose the tax deduction.</div>", unsafe_allow_html=True)


def page_statements():
    st.markdown("<div class='eyebrow'>Statements</div>", unsafe_allow_html=True)
    st.markdown("# Your books, in plain sight")
    a, b = st.columns(2)
    with a:
        st.markdown("#### Profit & Loss (accrual)")
        st.dataframe(pd.DataFrame({"Line": ["Revenue (billed)", "Total expenses", "Net profit"],
                                   "Amount": [format_inr(S["revenue"]), format_inr(-S["expenses"]),
                                              format_inr(S["net"])]}),
                     use_container_width=True, hide_index=True)
        if not exp.empty:
            by_cat = exp.groupby("category")["amount"].sum().sort_values(ascending=False)
            st.markdown("###### Expenses by category")
            st.bar_chart(by_cat.rename("₹"), color=BRASS, height=240)
    with b:
        st.markdown("#### Cash flow (actual)")
        st.dataframe(pd.DataFrame({
            "Line": ["Cash collected", "Cash paid out", "Net cash movement",
                     "Still to collect", "Still to pay (MSME, at risk)"],
            "Amount": [format_inr(S["cash_in"]), format_inr(-S["cash_out"]), format_inr(S["net_cash"]),
                       format_inr(S["outstanding"]), format_inr(S["payable_risk"])]}),
            use_container_width=True, hide_index=True)
        st.markdown("###### Cash in vs out")
        st.bar_chart(pd.DataFrame({"₹": [S["cash_in"], S["cash_out"]]},
                                  index=["Collected", "Paid out"]), color=INK, height=240)


def page_munshi():
    st.markdown("<div class='eyebrow'>Ask Munshi</div>", unsafe_allow_html=True)
    st.markdown("# Your accountant's briefing")
    st.markdown("<div class='small'>Munshi reads the figures your books produced and explains them in "
                "plain language. Turn on the AI narrative below for a richer read.</div>",
                unsafe_allow_html=True)
    st.write("")
    with st.expander("Optional: connect an Anthropic API key for AI narrative"):
        api_key = st.text_input("Anthropic API key", type="password",
                                placeholder="sk-ant-...  (kept only in this session)")
        model = st.text_input("Model", "claude-haiku-4-5-20251001")
        run_ai = st.button("Generate AI briefing")
    if run_ai and api_key:
        with st.spinner("Munshi is reading your books…"):
            try:
                st.markdown(f"<div class='munshi'>{get_ai_briefing(api_key, model, summary_text_for_llm(inv, exp, S))}</div>",
                            unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Couldn't reach the model ({e}). Showing the built-in read instead.")

    st.markdown("#### The deterministic read (always on)")
    for level, text in generate_insights(inv, exp, S):
        st.markdown(f"<div class='munshi'><span class='chip {level}'>{level.upper()}</span>{text}</div>",
                    unsafe_allow_html=True)


{"Dashboard": page_dashboard, "Receivables": page_receivables, "Expenses": page_expenses,
 "Financial statements": page_statements, "Ask Munshi": page_munshi}[page]()
