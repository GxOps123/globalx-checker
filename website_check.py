# app.py
import streamlit as st
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import pytz
import re
import concurrent.futures # Import for multi-threading

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Global X Monitor", page_icon="⚡", layout="wide")
st.title("⚡ Global X Australia - Speed Monitor")
st.markdown("Live tracking of NAV, Performance, Holdings, and Distribution updates.")

# --- CONFIGURATION ---
BASE_URL = "https://www.globalxetfs.com.au/funds/"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
SYD_TZ = pytz.timezone('Australia/Sydney')
EXCEPTION_FUNDS = ['USTB', 'BCOM', 'USIG']
FORCE_LIST = ['ETPMAG', 'ETPMPD', 'ETPMPM', 'ETPMPT']

# --- CORE LOGIC (V18 STABLE) ---
def parse_date(text):
    try:
        # Regex to extract date format like "24 Nov 2025"
        cln = re.sub(r'(?i)(date|data)\s+as\s+of\s+', '', text).strip().split(',')[0].strip()
        return datetime.strptime(cln, '%d %b %Y').date(), cln
    except: return None, text

def get_expectations(ticker):
    now_syd = datetime.now(SYD_TZ).date()
    # Calculate previous month end for Distributions
    first = now_syd.replace(day=1)
    last_month = first - timedelta(days=1)
    while last_month.weekday() > 4: last_month -= timedelta(days=1)
    
    # Business Rules: Exceptions are T-2, Standards are T-1
    if ticker.upper() in EXCEPTION_FUNDS:
        return get_last_bd(now_syd, 2), get_last_bd(now_syd, 1), last_month
    else:
        return get_last_bd(now_syd, 1), now_syd, last_month

def get_last_bd(d, n):
    curr = d
    c = 0
    while c < n:
        curr -= timedelta(days=1)
        if curr.weekday() < 5: c += 1
    return curr

@st.cache_data(ttl=3600) # Cache the fund list for 1 hour
def get_all_tickers():
    try:
        r = requests.get(BASE_URL, headers=HEADERS)
        matches = re.findall(r"/funds/([a-zA-Z0-9]{3,6})/", r.text)
        candidates = set([m.upper() for m in matches])
        for f in FORCE_LIST: candidates.add(f)
        BLACKLIST = ['INDEX', 'ABOUT', 'MEDIA', 'LOGIN', 'TERMS', 'PRIVACY', 'ADMIN', 'FUNDS']
        return sorted([t for t in candidates if t not in BLACKLIST and len(t) >= 3])
    except: return FORCE_LIST + ['ACDC', 'BANK']

def check_fund(ticker):
    # This function is executed in parallel threads
    url = f"{BASE_URL}{ticker.lower()}/"
    exp_nav, exp_hold, exp_dist = get_expectations(ticker)
    report = {'Ticker': ticker, 'NAV': 'Checking...', 'Holdings': 'Checking...', 'Perf': 'Checking...', 'Dist': 'Checking...'}
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')

        # Gather all text nodes that look like dates
        all_dates = []
        candidates = soup.find_all(['span', 'div'])
        for c in candidates:
            if "as of" in c.get_text().lower():
                dt, s = parse_date(c.get_text())
                if dt:
                    # Capture context (Parent + Grandparent text) to identify what date this is
                    pt = c.parent.parent.get_text(" ", strip=True).lower() if c.parent and c.parent.parent else ""
                    gt = c.parent.parent.parent.get_text(" ", strip=True).lower() if c.parent and c.parent.parent and c.parent.parent.parent else ""
                    all_dates.append({'dt': dt, 's': s, 'ctx': pt + " " + gt})

        nav_res, perf_res, hold_res = None, None, None
        
        # 1. NAV Check (Look for 'nav' keywords)
        for x in all_dates:
            if "nav" in x['ctx'] or "net asset" in x['ctx']:
                if nav_res is None or x['dt'] > nav_res['dt']: nav_res = x
        
        # 2. Performance Check (Look for 'return' keywords)
        for x in all_dates:
            if "return" in x['ctx']:
                if perf_res is None or x['dt'] > perf_res['dt']: perf_res = x

        # 3. Holdings Check (Look for 'holding' keywords)
        for x in all_dates:
            if ("holding" in x['ctx'] or "characteristics" in x['ctx']) and "return" not in x['ctx']:
                if hold_res is None or x['dt'] > hold_res['dt']: hold_res = x

        # Assign Results with Logic Checks
        if nav_res: report['NAV'] = f"✅ {nav_res['s']}" if nav_res['dt'] >= exp_nav else f"🔴 {nav_res['s']} (Late)"
        else: report['NAV'] = "⚠️ Missing"

        if perf_res: report['Perf'] = f"✅ {perf_res['s']}" if perf_res['dt'] >= exp_nav else f"🔴 {perf_res['s']} (Late)"
        else: report['Perf'] = "⚠️ Missing"

        if hold_res: report['Holdings'] = f"✅ {hold_res['s']}" if hold_res['dt'] >= exp_hold else f"🔴 {hold_res['s']} (Late)"
        else: report['Holdings'] = "⚠️ Missing"

        # Distribution Check (Simple text search)
        exp_s = exp_dist.strftime('%d %b %Y')
        if exp_s in soup.get_text(): report['Dist'] = f"✅ {exp_s}"
        else: report['Dist'] = "⚠️ Missing"

    except: report['NAV'] = "❌ Error"
    return report

# --- SIDEBAR & EXECUTION ---
with st.sidebar:
    st.header("Controls")
    if st.button("🚀 RUN FAST CHECK", type="primary"):
        run_check = True
    else:
        run_check = False
    st.info(f"Time: {datetime.now(SYD_TZ).strftime('%H:%M')}")
    st.markdown("---")
    st.markdown("**Legend:**")
    st.success("✅ Up to date")
    st.error("🔴 Late / Not updated")
    st.warning("⚠️ Tag not found")

if run_check:
    funds = get_all_tickers()
    st.toast(f"Found {len(funds)} funds. Speed scanning...")
    
    results = []
    
    # --- MULTI-THREADING EXECUTION ---
    # Using 10 workers for faster processing
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {executor.submit(check_fund, t): t for t in funds}
        # Iterate over completed futures
        for future in concurrent.futures.as_completed(future_to_url):
            data = future.result()
            results.append(data)
    
    # Sort results alphabetically by Ticker
    results = sorted(results, key=lambda x: x['Ticker'])
    
    df = pd.DataFrame(results)
    
    # Apply Styling logic
    def style_rows(val):
        s = str(val)
        if '🔴' in s: return 'background-color: #ffe6e6; color: #cc0000; font-weight: bold'
        if '✅' in s: return 'color: green; font-weight: bold'
        if '⚠️' in s: return 'color: orange'
        return ''

    st.dataframe(df.style.applymap(style_rows), use_container_width=True, height=1000)
    st.success("✨ Check Complete!")
else:
    st.info("👋 Ready. Click the button in the sidebar to start scanning.")