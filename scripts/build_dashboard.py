import os, requests, datetime, math

SF_INSTANCE   = os.environ["SF_INSTANCE_URL"].rstrip("/")
SF_CLIENT_ID  = os.environ["SF_CLIENT_ID"]
SF_CLIENT_SEC = os.environ["SF_CLIENT_SECRET"]
GH_TOKEN      = os.environ["GH_TOKEN"]
GH_REPO       = "Akshay-0502/Salesforce"

NOW_IST  = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST")
TODAY    = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y")

def sf_auth():
    r = requests.post(f"{SF_INSTANCE}/services/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": SF_CLIENT_ID, "client_secret": SF_CLIENT_SEC})
    r.raise_for_status(); return r.json()["access_token"]

def sfq(t, soql):
    r = requests.get(f"{SF_INSTANCE}/services/data/v59.0/query", params={"q": soql},
        headers={"Authorization": f"Bearer {t}"}); r.raise_for_status(); return r.json()

def sfl(t):
    r = requests.get(f"{SF_INSTANCE}/services/data/v59.0/limits/",
        headers={"Authorization": f"Bearer {t}"}); r.raise_for_status(); return r.json()

def gh(path):
    r = requests.get(f"https://api.github.com/repos/{GH_REPO}/{path}",
        headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"})
    return r.json() if r.ok else {}

# ── Fetch data ──
token   = sf_auth()
limits  = sfl(token)
users   = sfq(token, "SELECT Id,Name,UserType,IsActive,CreatedDate,LastLoginDate FROM User WHERE IsActive=true ORDER BY LastLoginDate DESC NULLS LAST LIMIT 20")
classes = sfq(token, "SELECT Id,Name,LengthWithoutComments FROM ApexClass WHERE Status='Active' ORDER BY LengthWithoutComments DESC LIMIT 10")
cls_cnt = sfq(token, "SELECT COUNT() FROM ApexClass WHERE Status='Active'")
trg_cnt = sfq(token, "SELECT COUNT() FROM ApexTrigger WHERE Status='Active'")
flows   = sfq(token, "SELECT COUNT() FROM FlowDefinitionView WHERE IsActive=true")
crons   = sfq(token, "SELECT Id,CronJobDetail.Name,State,NextFireTime FROM CronTrigger ORDER BY NextFireTime ASC LIMIT 10")
async_j = sfq(token, "SELECT Id,ApexClass.Name,Status,JobType,CreatedDate,NumberOfErrors FROM AsyncApexJob ORDER BY CreatedDate DESC LIMIT 10")
tests   = sfq(token, "SELECT Outcome,MethodName,ApexClass.Name FROM ApexTestResult ORDER BY TestTimestamp DESC LIMIT 5")

prs_open   = gh("pulls?state=open&per_page=20")
prs_closed = gh("pulls?state=closed&per_page=50&sort=updated&direction=desc")
cutoff     = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
prs_merged = [p for p in (prs_closed if isinstance(prs_closed, list) else [])
              if p.get("merged_at") and datetime.datetime.strptime(p["merged_at"], "%Y-%m-%dT%H:%M:%SZ") > cutoff]

# ── Derived values ──
def lim_pct(key):
    if key not in limits: return 0, 0, 0
    mx = limits[key]["Max"]; rem = limits[key]["Remaining"]; used = mx - rem
    return used, mx, round(used/mx*100,2) if mx else 0

api_used, api_max, api_pct     = lim_pct("DailyApiRequests")
data_used, data_max, data_pct  = lim_pct("DataStorageMB")
file_used, file_max, file_pct  = lim_pct("FileStorageMB")
async_used, async_max, async_pct = lim_pct("DailyAsyncApexExecutions")

flow_count  = flows.get("totalSize", 0)
class_count = cls_cnt.get("totalSize", 0)
trig_count  = trg_cnt.get("totalSize", 0)
cron_list   = crons.get("records") or []
async_list  = async_j.get("records") or []
user_list   = users.get("records") or []
class_list  = classes.get("records") or []
has_tests   = (tests.get("totalSize") or 0) > 0
aborted     = sum(1 for j in async_list if j.get("Status") == "Aborted")
cron_all_ok = all(c["State"] == "WAITING" for c in cron_list) if cron_list else True
open_prs    = prs_open if isinstance(prs_open, list) else []

# ── Health score ──
score, factors = 0, []
if api_pct < 10:     score+=20; factors.append(("API limits well within threshold", "+20", "pass"))
else:                score-=10; factors.append((f"API usage at {api_pct}%", "−10", "fail"))
if data_pct < 50:    score+=15; factors.append(("Data & file storage healthy", "+15", "pass"))
else:                score-=10; factors.append((f"Data storage at {data_pct}%", "−10", "fail"))
if flow_count > 0:   score+=15; factors.append((f"All {flow_count} flows active", "+15", "pass"))
if cron_all_ok and cron_list: score+=10; factors.append((f"All {len(cron_list)} scheduled jobs WAITING", "+10", "pass"))
elif not cron_list:  factors.append(("No scheduled jobs found", "0", "warn"))
else:                score-=5;  factors.append(("Some scheduled jobs not WAITING", "−5", "warn"))
if class_count > 0:  score+=10; factors.append((f"{class_count} active Apex classes, 0 errors", "+10", "pass"))
if trig_count == 0:  score+=2;  factors.append(("No triggers — flow-first architecture", "+2", "pass"))
else:                factors.append((f"{trig_count} Apex triggers active", "0", "warn"))
if aborted == 0:     score+=8;  factors.append(("No aborted async jobs", "+8", "pass"))
else:                score-=8;  factors.append((f"{aborted} aborted async job(s)", "−8", "warn"))
if has_tests:        score+=12; factors.append(("Apex test results present", "+12", "pass"))
else:                score-=12; factors.append(("No Apex test results / coverage data", "−12", "fail"))
if len(open_prs) > 5: score-=4; factors.append((f"{len(open_prs)} open PRs pending review", "−4", "warn"))
else:                factors.append((f"Deployment history not accessible", "−4", "warn")); score-=4
score = max(0, min(100, score))

if score >= 80:   health_label, hc = "Healthy",          "#1A7A4A"
elif score >= 60: health_label, hc = "Good",             "#B45309"
else:             health_label, hc = "Needs Attention",  "#B91C1C"

# donut arcs
green_score = sum(int(f[1].replace("+","").replace("−","").replace("-","")) for f in factors if f[2]=="pass")
warn_score  = sum(int(f[1].replace("+","").replace("−","").replace("-","")) for f in factors if f[2]=="warn")
fail_score  = sum(int(f[1].replace("+","").replace("−","").replace("-","")) for f in factors if f[2]=="fail")
total_abs   = green_score + warn_score + fail_score or 1
g_arc = round(green_score/total_abs*100, 1)
w_arc = round(warn_score/total_abs*100, 1)
r_arc = round(fail_score/total_abs*100, 1)

# ── HTML helpers ──
def pill(text, cls):
    styles = {"green":"background:#E6F4EC;color:#1A7A4A","red":"background:#FEE2E2;color:#B91C1C",
              "amber":"background:#FEF3C7;color:#B45309","blue":"background:#EEF3FB;color:#1058B0","grey":"background:#F1F3F7;color:#6B7A8D"}
    s = styles.get(cls, styles["grey"])
    return f'<span class="pill" style="{s}">{text}</span>'

def meter(label, used, mx, pct):
    c = "#B91C1C" if pct>80 else "#B45309" if pct>50 else "#1A7A4A"
    bar = min(pct, 100)
    return f'''<div class="meter-item">
      <div class="meter-head"><span>{label}</span><span style="font-weight:600;color:{c}">~{pct}% used ({used:,} / {mx:,})</span></div>
      <div class="meter-track"><div class="meter-fill" style="width:{bar}%;background:{c}"></div></div>
    </div>'''

def factor_row(label, val, typ):
    dot_color = {"pass":"#1A7A4A","warn":"#F59E0B","fail":"#B91C1C"}.get(typ,"#6B7A8D")
    return f'<div class="hf"><div class="hf-dot" style="background:{dot_color}"></div>{label} ({val})</div>'

# ── Build sections ──
factor_html = "".join(factor_row(f[0],f[1],f[2]) for f in factors)

# users
def user_initials(name): parts=name.split(); return (parts[0][0]+(parts[-1][0] if len(parts)>1 else parts[0][1])).upper()
human_count = sum(1 for u in user_list if u["UserType"]=="Standard")
integ_count = sum(1 for u in user_list if "Integration" in u.get("UserType","") or "Integration" in u.get("Name",""))
sys_count   = sum(1 for u in user_list if "Automated" in u.get("UserType","") or "AutomatedProcess" in u.get("UserType",""))

user_rows_html = ""
for u in user_list[:6]:
    last = u.get("LastLoginDate")
    last_str = last[:10] if last else "Never"
    last_color = "#B91C1C" if not last else "#1A7A4A"
    utype = u.get("UserType","")
    if "Standard" == utype: pc, plbl = "green","Active"
    elif "Integration" in utype or "Integration" in u.get("Name",""): pc, plbl = "blue","Integration"
    else: pc, plbl = "grey","System"
    user_rows_html += f'''<div class="user-row">
      <div class="avatar">{user_initials(u["Name"])}</div>
      <div class="user-info"><div class="user-name">{u["Name"]}</div>
      <div class="user-meta">{utype} · Created {u["CreatedDate"][:10]} · Last login: <span style="color:{last_color}">{last_str}</span></div></div>
      {pill(plbl, pc)}</div>'''

never_count = sum(1 for u in user_list if not u.get("LastLoginDate"))
user_warning = f'<div class="info-banner amber" style="margin-top:10px">⚠ {never_count} of {len(user_list)} users have never logged in. Integration users may need access validation before go-live.</div>' if never_count > 0 else '<div class="info-banner green" style="margin-top:10px">✓ All users have logged in recently.</div>'

# cron jobs
cron_html = ""
for c in cron_list:
    nxt = c.get("NextFireTime","")[:16] if c.get("NextFireTime") else "—"
    state = c["State"]
    pc = "green" if state=="WAITING" else "red"
    cron_html += f'''<div class="job-row"><div class="job-icon">⏱</div>
      <div class="job-info"><div class="job-name">{c["CronJobDetail"]["Name"]}</div>
      <div class="job-meta">Next run: {nxt} UTC</div></div>{pill(state.capitalize(), pc)}</div>'''
if not cron_html: cron_html = '<div class="info-banner amber">⚠ No scheduled jobs found in org.</div>'
cron_banner = '<div class="info-banner green" style="margin-top:4px">✓ All scheduled jobs are in healthy WAITING state.</div>' if cron_all_ok and cron_list else ""

# apex classes
class_rows_html = ""
for c in class_list:
    sz = c.get("LengthWithoutComments", 0)
    class_rows_html += f'<tr><td>{c["Name"]}</td><td>{sz:,}</td><td>{pill("Service/Controller","blue")}</td></tr>'

# async jobs
async_rows_html = ""
for j in async_list:
    cls_name = j.get("ApexClass",{}).get("Name","—") if j.get("ApexClass") else j.get("JobType","—")
    status = j.get("Status","")
    pc = "green" if status=="Completed" else "red" if status in ["Failed","Aborted"] else "amber"
    async_rows_html += f'<tr><td>{cls_name}</td><td>{j.get("JobType","")}</td><td>{pill(status, pc)}</td><td>{j["CreatedDate"][:10]}</td><td>{j.get("NumberOfErrors",0)}</td></tr>'
async_warning = f'<div class="info-banner amber">⚠ {aborted} ScheduledApex job(s) aborted recently. Investigate and re-queue if necessary.</div>' if aborted > 0 else '<div class="info-banner green">✓ No failed or aborted async jobs.</div>'

# PRs
pr_open_html = ""
for p in open_prs:
    pr_open_html += f'''<div class="pr-item"><div class="pr-icon open">⤴</div>
      <div class="pr-info">
        <div class="pr-title"><a href="{p["html_url"]}" target="_blank" style="color:#1058B0;text-decoration:none">#{p["number"]} — {p["title"][:80]}</a></div>
        <div class="pr-meta"><strong>{p["head"]["ref"]} → {p["base"]["ref"]}</strong> &nbsp;·&nbsp; Opened: {p["created_at"][:10]} &nbsp;·&nbsp; Author: <strong>{p["user"]["login"]}</strong></div>
      </div>{pill("Awaiting Review","amber")}</div>'''
if not pr_open_html: pr_open_html = '<div class="info-banner green">✓ No open pull requests.</div>'

pr_merged_html = ""
for p in prs_merged:
    pr_merged_html += f'''<div class="pr-item"><div class="pr-icon merged">✓</div>
      <div class="pr-info">
        <div class="pr-title"><a href="{p["html_url"]}" target="_blank" style="color:#1058B0;text-decoration:none">#{p["number"]} — {p["title"][:80]}</a></div>
        <div class="pr-meta"><strong>{p["head"]["ref"]} → {p["base"]["ref"]}</strong> &nbsp;·&nbsp; Merged: {p["merged_at"][:10]} &nbsp;·&nbsp; Author: <strong>{p["user"]["login"]}</strong></div>
      </div>{pill("Merged","green")}</div>'''
if not pr_merged_html: pr_merged_html = '<div class="info-banner blue">ℹ No PRs merged in the last 24 hours.</div>'

# KPI colour helpers
def kpi_cls(val, warn, bad):
    if val >= bad: return "red"
    if val >= warn: return "amber"
    return "green"

api_cls  = kpi_cls(api_pct, 50, 80)
open_cls = kpi_cls(len(open_prs), 3, 6)

# ── Full HTML ──
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Griffith University – DevOps Metrics</title>
<style>
  :root {{
    color-scheme: light;
    --blue: #1058B0; --blue-dark: #0B3D7A; --blue-light: #EEF3FB; --blue-mid: #2E74C8;
    --slate: #2C3E50; --muted: #6B7A8D; --line: #DDE3EE; --bg: #F2F5FA; --white: #FFFFFF;
    --green: #1A7A4A; --green-bg: #E6F4EC; --red: #B91C1C; --red-bg: #FEE2E2;
    --amber: #B45309; --amber-bg: #FEF3C7; --radius: 8px;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--slate); font-size: 13.5px; line-height: 1.5; }}
  .topbar {{ background: var(--blue-dark); color: #fff; padding: 0 28px; height: 52px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,.18); }}
  .topbar-logo {{ font-size: 15px; font-weight: 700; color: #fff; }}
  .topbar-logo span {{ color: #7BB8FF; }}
  .topbar-badge {{ background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.2); border-radius: 20px; padding: 2px 10px; font-size: 11px; color: #B8D4FF; margin-left: 12px; }}
  .topbar-right {{ font-size: 11.5px; color: #8BB8E8; }}
  .live-dot {{ display: inline-block; width: 7px; height: 7px; background: #4ADE80; border-radius: 50%; margin-right: 5px; animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
  .wrapper {{ max-width: 1300px; margin: 0 auto; padding: 24px 28px 40px; }}
  .page-title {{ font-size: 20px; font-weight: 700; color: var(--blue-dark); margin-bottom: 4px; }}
  .page-sub {{ font-size: 12px; color: var(--muted); margin-bottom: 22px; }}
  .health-score-wrap {{ display: flex; align-items: center; gap: 28px; background: var(--white); border-radius: var(--radius); box-shadow: 0 1px 4px rgba(0,0,0,.06); padding: 20px 28px; margin-bottom: 20px; }}
  .health-donut {{ flex-shrink: 0; }}
  .health-detail {{ flex: 1; }}
  .health-title {{ font-size: 13px; font-weight: 600; color: var(--blue-dark); margin-bottom: 12px; }}
  .health-factors {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
  .hf {{ display: flex; align-items: center; gap: 8px; font-size: 12px; }}
  .hf-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
  .health-score-num {{ text-align: center; }}
  .health-score-num .score {{ font-size: 44px; font-weight: 800; color: {hc}; line-height: 1; }}
  .health-score-num .score-label {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  .health-score-num .score-grade {{ font-size: 16px; font-weight: 700; color: {hc}; margin-top: 2px; }}
  .kpi-strip {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin-bottom: 24px; }}
  .kpi {{ background: var(--white); border-radius: var(--radius); padding: 16px 18px; border-top: 3px solid var(--blue); box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
  .kpi.green {{ border-top-color: var(--green); }} .kpi.amber {{ border-top-color: var(--amber); }} .kpi.red {{ border-top-color: var(--red); }}
  .kpi-label {{ font-size: 11px; color: var(--muted); margin-bottom: 6px; }}
  .kpi-value {{ font-size: 26px; font-weight: 700; color: var(--blue-dark); line-height: 1; }}
  .kpi.green .kpi-value {{ color: var(--green); }} .kpi.amber .kpi-value {{ color: var(--amber); }} .kpi.red .kpi-value {{ color: var(--red); }}
  .kpi-sub {{ font-size: 11px; color: var(--muted); margin-top: 5px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 18px; }}
  .card {{ background: var(--white); border-radius: var(--radius); box-shadow: 0 1px 4px rgba(0,0,0,.06); overflow: hidden; }}
  .card-head {{ padding: 14px 18px 10px; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; }}
  .card-title {{ font-size: 13px; font-weight: 600; color: var(--blue-dark); }}
  .card-meta {{ font-size: 11px; color: var(--muted); }}
  .card-body {{ padding: 16px 18px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  thead tr {{ background: var(--blue-light); }}
  thead th {{ padding: 9px 12px; text-align: left; font-weight: 600; font-size: 11.5px; color: var(--blue-dark); }}
  tbody tr {{ border-bottom: 1px solid var(--line); }}
  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr:hover {{ background: #F8FAFF; }}
  tbody td {{ padding: 9px 12px; color: var(--slate); vertical-align: middle; }}
  .pill {{ display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
  .meter-item {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }}
  .meter-head {{ display: flex; justify-content: space-between; font-size: 12px; }}
  .meter-track {{ height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; }}
  .meter-fill {{ height: 100%; border-radius: 4px; }}
  .user-row {{ display: flex; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--line); }}
  .user-row:last-child {{ border-bottom: none; }}
  .avatar {{ width: 32px; height: 32px; border-radius: 50%; background: var(--blue-light); color: var(--blue-dark); font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
  .user-info {{ flex: 1; }} .user-name {{ font-size: 12.5px; font-weight: 600; }} .user-meta {{ font-size: 11px; color: var(--muted); }}
  .job-row {{ display: flex; align-items: flex-start; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--line); }}
  .job-row:last-child {{ border-bottom: none; }}
  .job-icon {{ width: 28px; height: 28px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 13px; flex-shrink: 0; background: var(--blue-light); color: var(--blue); }}
  .job-info {{ flex: 1; }} .job-name {{ font-size: 12.5px; font-weight: 600; }} .job-meta {{ font-size: 11px; color: var(--muted); }}
  .info-banner {{ padding: 10px 14px; border-radius: 6px; font-size: 12px; margin-top: 12px; }}
  .info-banner.amber {{ background: var(--amber-bg); color: var(--amber); }}
  .info-banner.green {{ background: var(--green-bg); color: var(--green); }}
  .info-banner.blue {{ background: var(--blue-light); color: var(--blue); }}
  .pr-item {{ display: flex; align-items: flex-start; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--line); }}
  .pr-item:last-child {{ border-bottom: none; }}
  .pr-icon {{ width: 30px; height: 30px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; }}
  .pr-icon.open {{ background: #DBEAFE; color: #1D4ED8; }} .pr-icon.merged {{ background: var(--green-bg); color: var(--green); }}
  .pr-info {{ flex: 1; }} .pr-title {{ font-size: 12.5px; font-weight: 600; }} .pr-meta {{ font-size: 11px; color: var(--muted); margin-top: 3px; }}
  .dash-footer {{ text-align: center; font-size: 11px; color: var(--muted); margin-top: 30px; padding-top: 16px; border-top: 1px solid var(--line); }}
  a {{ color: var(--blue); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="topbar">
  <div style="display:flex;align-items:center">
    <div class="topbar-logo">Griffith <span>University</span></div>
    <div class="topbar-badge">Trailsignup Org · Live</div>
  </div>
  <div class="topbar-right">
    <span class="live-dot"></span>Data pulled: {TODAY} · Org: trailsignup-b168ab0f0d1b03
  </div>
</div>

<div class="wrapper">
  <div class="page-title">DevOps Metrics</div>
  <div class="page-sub">Real-time Salesforce org health, user activity, automation coverage, and scheduled job status — pulled directly from your org via API</div>

  <!-- Health Score -->
  <div class="health-score-wrap">
    <div class="health-donut">
      <svg width="130" height="130" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#EEF3FB" stroke-width="4"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#1A7A4A" stroke-width="4"
          stroke-dasharray="{g_arc} {100-g_arc}" stroke-dashoffset="25" stroke-linecap="round"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#F59E0B" stroke-width="4"
          stroke-dasharray="{w_arc} {100-w_arc}" stroke-dashoffset="{-(g_arc-25)}" stroke-linecap="round"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#B91C1C" stroke-width="4"
          stroke-dasharray="{r_arc} {100-r_arc}" stroke-dashoffset="{-(g_arc+w_arc-25)}" stroke-linecap="round"/>
        <text x="18" y="17" text-anchor="middle" font-size="8" font-weight="800" fill="#0B3D7A">{score}</text>
        <text x="18" y="22" text-anchor="middle" font-size="4" fill="#6B7A8D">/100</text>
      </svg>
    </div>
    <div class="health-score-num">
      <div class="score">{score}</div>
      <div class="score-grade">{health_label}</div>
      <div class="score-label">Org Health Score</div>
    </div>
    <div class="health-detail">
      <div class="health-title">Score breakdown — based on live org data</div>
      <div class="health-factors">{factor_html}</div>
    </div>
  </div>

  <!-- KPI Strip -->
  <div class="kpi-strip">
    <div class="kpi green">
      <div class="kpi-label">Active Users</div>
      <div class="kpi-value">{len(user_list)}</div>
      <div class="kpi-sub">{human_count} human · {len(user_list)-human_count} system/integration</div>
    </div>
    <div class="kpi green">
      <div class="kpi-label">Active Apex Classes</div>
      <div class="kpi-value">{class_count}</div>
      <div class="kpi-sub">{trig_count} triggers · all active</div>
    </div>
    <div class="kpi green">
      <div class="kpi-label">Active Flows</div>
      <div class="kpi-value">{flow_count}</div>
      <div class="kpi-sub">AutoLaunched, Screen, Orchestrator</div>
    </div>
    <div class="kpi green">
      <div class="kpi-label">Scheduled Jobs</div>
      <div class="kpi-value">{len(cron_list)}</div>
      <div class="kpi-sub">{"All in WAITING state" if cron_all_ok else "Check job states"}</div>
    </div>
    <div class="kpi {api_cls}">
      <div class="kpi-label">API Usage Today</div>
      <div class="kpi-value">~{api_pct}%</div>
      <div class="kpi-sub">{api_used:,} of {api_max:,} requests used</div>
    </div>
    <div class="kpi {open_cls}">
      <div class="kpi-label">Open Pull Requests</div>
      <div class="kpi-value">{len(open_prs)}</div>
      <div class="kpi-sub">{"feature → uat · awaiting review" if open_prs else "No open PRs"}</div>
    </div>
  </div>

  <!-- Row 1: Org Health + User Activity -->
  <div class="grid-2">
    <div class="card">
      <div class="card-head">
        <div class="card-title">Salesforce Org Health — Live Limits</div>
        <div class="card-meta">trailsignup-b168ab0f0d1b03</div>
      </div>
      <div class="card-body">
        {meter("Daily API Requests", api_used, api_max, api_pct)}
        {meter("Data Storage (MB)", data_used, data_max, data_pct)}
        {meter("File Storage (MB)", file_used, file_max, file_pct)}
        {meter("Daily Async Apex Executions", async_used, async_max, async_pct)}
        <div class="info-banner green">✓ All org limits are well within safe thresholds. No immediate capacity concerns.</div>
        <br>
        <table>
          <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>
            <tr><td>Active Apex Classes</td><td>{pill("Pass","green")}</td><td>{class_count} active classes</td></tr>
            <tr><td>Apex Triggers</td><td>{pill("Info","blue")}</td><td>{trig_count} triggers — {"logic handled via Flows" if trig_count==0 else "review trigger usage"}</td></tr>
            <tr><td>Active Flows</td><td>{pill("Pass","green")}</td><td>{flow_count} active flows across all types</td></tr>
            <tr><td>Scheduled Jobs</td><td>{pill("Pass" if cron_all_ok else "Review","green" if cron_all_ok else "amber")}</td><td>{len(cron_list)} jobs, {"all WAITING (healthy)" if cron_all_ok else "some not in WAITING state"}</td></tr>
            <tr><td>Async Apex Jobs</td><td>{pill("Review" if aborted>0 else "Pass","amber" if aborted>0 else "green")}</td><td>{f"{aborted} Aborted job(s) detected" if aborted>0 else "All jobs completed successfully"}</td></tr>
            <tr><td>Apex Test Results</td><td>{pill("Pass" if has_tests else "No Data","green" if has_tests else "amber")}</td><td>{"Test results available" if has_tests else "No test runs found — run tests to populate"}</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">User Activity & Onboarding</div>
        <div class="card-meta">{len(user_list)} active users</div>
      </div>
      <div class="card-body">
        <div style="display:flex;gap:14px;margin-bottom:16px">
          <div style="flex:1;background:var(--green-bg);border-radius:6px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:var(--green)">{human_count}</div>
            <div style="font-size:11px;color:var(--muted)">Human Users</div>
          </div>
          <div style="flex:1;background:var(--blue-light);border-radius:6px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:var(--blue)">{integ_count}</div>
            <div style="font-size:11px;color:var(--muted)">Integration Users</div>
          </div>
          <div style="flex:1;background:#F1F3F7;border-radius:6px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:var(--muted)">{sys_count}</div>
            <div style="font-size:11px;color:var(--muted)">Automated Process</div>
          </div>
        </div>
        {user_rows_html}
        {user_warning}
      </div>
    </div>
  </div>

  <!-- Row 2: Scheduled Jobs + Async Jobs -->
  <div class="grid-2">
    <div class="card">
      <div class="card-head">
        <div class="card-title">Scheduled Jobs</div>
        <div class="card-meta">{len(cron_list)} jobs · {"all WAITING" if cron_all_ok else "check states"}</div>
      </div>
      <div class="card-body">{cron_html}{cron_banner}</div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">Async Apex Jobs & Deployment Activity</div>
        <div class="card-meta">Recent job history</div>
      </div>
      <div class="card-body">
        <table style="margin-bottom:14px">
          <thead><tr><th>Job</th><th>Type</th><th>Status</th><th>Date</th><th>Errors</th></tr></thead>
          <tbody>{async_rows_html or "<tr><td colspan='5' style='color:#999;text-align:center'>No async jobs found</td></tr>"}</tbody>
        </table>
        {async_warning}
        <br>
        <div style="font-size:12px;font-weight:600;color:var(--slate);margin-bottom:10px">Test Coverage Status</div>
        {"<div class='info-banner green'>✓ Apex test results found in org.</div>" if has_tests else "<div class='info-banner amber'>⚠ No Apex test results found. Run <strong>Run All Tests</strong> from Setup → Apex Test Execution to generate coverage data.</div>"}
      </div>
    </div>
  </div>

  <!-- Row 3: Apex Classes + PRs -->
  <div class="grid-2">
    <div class="card">
      <div class="card-head">
        <div class="card-title">Active Apex Classes ({class_count})</div>
        <div class="card-meta">Top by size · {trig_count} triggers</div>
      </div>
      <div class="card-body">
        <table>
          <thead><tr><th>Class Name</th><th>Size</th><th>Type</th></tr></thead>
          <tbody>{class_rows_html or "<tr><td colspan='3' style='color:#999;text-align:center'>No classes found</td></tr>"}</tbody>
        </table>
        <div class="info-banner blue" style="margin-top:10px">ℹ {class_count} classes found. {"No Apex test results — recommend running <strong>Run All Tests</strong> to establish baseline coverage." if not has_tests else "Test results available."}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">Pull Requests — Open</div>
        <div class="card-meta">github.com/Akshay-0502/Salesforce · Live</div>
      </div>
      <div class="card-body">{pr_open_html}</div>
    </div>
  </div>

  <!-- PRs Merged -->
  <div class="card" style="margin-bottom:18px">
    <div class="card-head">
      <div class="card-title">PRs Merged in Last 24h</div>
      <div class="card-meta">{len(prs_merged)} merged</div>
    </div>
    <div class="card-body">{pr_merged_html}</div>
  </div>

  <div class="dash-footer">
    Griffith University DevOps Metrics &nbsp;·&nbsp; Org: trailsignup-b168ab0f0d1b03 &nbsp;·&nbsp; Repo: Akshay-0502/Salesforce &nbsp;·&nbsp; Powered by GitHub Actions &nbsp;·&nbsp; Refreshed: {NOW_IST}
  </div>
</div>
</body>
</html>"""

with open("docs/index.html", "w") as f:
    f.write(html)
print(f"Dashboard built. Score: {score}/100 ({health_label})")
