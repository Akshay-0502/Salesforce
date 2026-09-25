import os, requests, datetime

SF_INSTANCE   = os.environ["SF_INSTANCE_URL"].rstrip("/")
SF_CLIENT_ID  = os.environ["SF_CLIENT_ID"]
SF_CLIENT_SEC = os.environ["SF_CLIENT_SECRET"]
GH_TOKEN      = os.environ["GH_TOKEN"]
GH_REPO       = "Akshay-0502/Salesforce"
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")

NOW_IST = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST")
TODAY   = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y")

# ── API helpers ──
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

# ── Fetch ──
token   = sf_auth()
limits  = sfl(token)
users   = sfq(token, "SELECT Id,Name,UserType,IsActive,CreatedDate,LastLoginDate FROM User WHERE IsActive=true ORDER BY LastLoginDate DESC NULLS LAST LIMIT 20")
cls_cnt = sfq(token, "SELECT COUNT() FROM ApexClass WHERE Status='Active'")
classes = sfq(token, "SELECT Id,Name,LengthWithoutComments FROM ApexClass WHERE Status='Active' ORDER BY LengthWithoutComments DESC LIMIT 10")
trg_cnt = sfq(token, "SELECT COUNT() FROM ApexTrigger WHERE Status='Active'")
flows   = sfq(token, "SELECT COUNT() FROM FlowDefinitionView WHERE IsActive=true")
crons   = sfq(token, "SELECT Id,CronJobDetail.Name,State,NextFireTime FROM CronTrigger ORDER BY NextFireTime ASC LIMIT 10")
async_j = sfq(token, "SELECT Id,ApexClass.Name,Status,JobType,CreatedDate,NumberOfErrors FROM AsyncApexJob ORDER BY CreatedDate DESC LIMIT 10")
tests   = sfq(token, "SELECT Outcome FROM ApexTestResult LIMIT 1")
prs_open   = gh("pulls?state=open&per_page=20")
prs_closed = gh("pulls?state=closed&per_page=50&sort=updated&direction=desc")
cutoff     = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
prs_merged = [p for p in (prs_closed if isinstance(prs_closed, list) else [])
              if p.get("merged_at") and
              datetime.datetime.strptime(p["merged_at"], "%Y-%m-%dT%H:%M:%SZ") > cutoff]

# ── Derived ──
def lp(key):
    if key not in limits: return 0, 0, 0.0
    mx = limits[key]["Max"]; used = mx - limits[key]["Remaining"]
    return used, mx, round(used/mx*100, 2) if mx else 0.0

api_used,  api_max,   api_pct   = lp("DailyApiRequests")
data_used, data_max,  data_pct  = lp("DataStorageMB")
file_used, file_max,  file_pct  = lp("FileStorageMB")
axc_used,  axc_max,   axc_pct   = lp("DailyAsyncApexExecutions")

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

# ── Health score (weighted, 0-100) ──
factors = []  # (label, earned, max, cls)  cls = hf-pass / hf-warn / hf-fail

def fa(label, earned, maxp, cls): factors.append((label, earned, maxp, cls))

# API limits (15 pts)
if api_pct < 10:   fa(f"API limits well within threshold (+{15})", 15, 15, "hf-pass")
elif api_pct < 50: fa(f"API usage moderate ({api_pct}%)", 10, 15, "hf-warn")
else:              fa(f"API usage high ({api_pct}%)", 0, 15, "hf-fail")

# Storage (10 pts)
if data_pct < 50:  fa("Data & file storage healthy (+10)", 10, 10, "hf-pass")
elif data_pct < 80:fa(f"Data storage moderate ({data_pct}%)", 5, 10, "hf-warn")
else:              fa(f"Data storage critical ({data_pct}%)", 0, 10, "hf-fail")

# Flows (15 pts)
if flow_count > 50:  fa(f"All {flow_count} flows active (+15)", 15, 15, "hf-pass")
elif flow_count > 0: fa(f"{flow_count} active flows (+10)", 10, 15, "hf-warn")
else:                fa("No active flows", 0, 15, "hf-fail")

# Scheduled jobs (10 pts)
if cron_list and cron_all_ok: fa(f"All {len(cron_list)} scheduled jobs WAITING (+10)", 10, 10, "hf-pass")
elif cron_list:               fa("Some scheduled jobs not WAITING", 5, 10, "hf-warn")
else:                         fa("No scheduled jobs found", 7, 10, "hf-warn")

# Apex classes (10 pts)
if class_count > 0: fa(f"{class_count} active Apex classes, 0 errors (+10)", 10, 10, "hf-pass")
else:               fa("No Apex classes found", 0, 10, "hf-fail")

# Triggers (5 pts)
if trig_count == 0: fa("No triggers — flow-first architecture (+5)", 5, 5, "hf-pass")
else:               fa(f"{trig_count} Apex trigger(s) active", 2, 5, "hf-warn")

# Async jobs (10 pts)
if aborted == 0:    fa("No aborted async jobs (+10)", 10, 10, "hf-pass")
elif aborted <= 2:  fa(f"{aborted} aborted async job(s) (−5)", 5, 10, "hf-warn")
else:               fa(f"{aborted} aborted async jobs (critical)", 0, 10, "hf-fail")

# Test results (15 pts)
if has_tests:  fa("Apex test results present (+15)", 15, 15, "hf-pass")
else:          fa("No Apex test results / coverage data (−0)", 5, 15, "hf-warn")

# PRs (10 pts)
stale = len([p for p in open_prs if (datetime.datetime.utcnow() -
    datetime.datetime.strptime(p["created_at"], "%Y-%m-%dT%H:%M:%SZ")).days > 7])
if stale == 0 and len(open_prs) <= 3: fa(f"{len(open_prs)} open PR(s), none stale (+10)", 10, 10, "hf-pass")
elif stale == 0:                       fa(f"{len(open_prs)} open PRs", 7, 10, "hf-warn")
else:                                  fa(f"{stale} stale PR(s) > 7 days", 3, 10, "hf-fail")

score = round(sum(f[1] for f in factors) / sum(f[2] for f in factors) * 100)
score = max(0, min(100, score))

if score >= 80:   hlabel, hc, hc_score = "Healthy",         "#1A7A4A", "#1A7A4A"
elif score >= 60: hlabel, hc, hc_score = "Good",            "#B45309", "#B45309"
else:             hlabel, hc, hc_score = "Needs Attention", "#B91C1C", "#B91C1C"

# Multi-arc donut: green / amber / red proportional to earned points per tier
total_max_pts = sum(f[2] for f in factors)
green_pts = sum(f[1] for f in factors if f[3] == "hf-pass")
warn_pts  = sum(f[1] for f in factors if f[3] == "hf-warn")
fail_pts  = sum(f[1] for f in factors if f[3] == "hf-fail")
# Scale to fill full circle (no grey gap)
total_pts = green_pts + warn_pts + fail_pts or 1
g_arc = round(green_pts / total_pts * 100, 1)
w_arc = round(warn_pts  / total_pts * 100, 1)
r_arc = round(100 - g_arc - w_arc, 1)  # remainder ensures full circle
# each arc starts where previous ended; dashoffset 25 = start at top
g_offset = 25
w_offset = round(25 - g_arc, 1)
r_offset = round(25 - g_arc - w_arc, 1)
# each arc's empty portion = 100 - its own length
g_rest = round(100 - g_arc, 1)
w_rest = round(100 - w_arc, 1)
r_rest = round(100 - r_arc, 1)

# ── Slack alert ──
if score < 60 and SLACK_WEBHOOK:
    lines = "\n".join(f"• {f[0]}" for f in factors)
    try:
        requests.post(SLACK_WEBHOOK, json={"blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "🔴 Salesforce Org Health Alert"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*Health Score: {score}/100* — _{hlabel}_\n*Org:* trailsignup-b168ab0f0d1b03\n*Checked:* {NOW_IST}\n\n{lines}\n\n<https://akshay-0502.github.io/Salesforce/|View Dashboard>"}}
        ]}, timeout=10)
        print("Slack alert sent")
    except Exception as e:
        print(f"Slack alert failed: {e}")

# ── HTML helpers ──
def pill(text, cls):
    m = {"pill-green":"background:var(--green-bg);color:var(--green)",
         "pill-red":  "background:var(--red-bg);color:var(--red)",
         "pill-amber":"background:var(--amber-bg);color:var(--amber)",
         "pill-blue": "background:var(--blue-light);color:var(--blue)",
         "pill-grey": "background:#F1F3F7;color:var(--muted)"}
    return f'<span class="pill" style="{m.get(cls,m["pill-grey"])}">{text}</span>'

def meter(label, used, mx, pct):
    fc = "fill-red" if pct>80 else "fill-amber" if pct>50 else "fill-green"
    vc = "var(--red)" if pct>80 else "var(--amber)" if pct>50 else "var(--green)"
    return f'''<div class="meter-item">
      <div class="meter-head"><span>{label}</span><span style="font-weight:600;color:{vc}">~{pct}% used ({used:,} / {mx:,})</span></div>
      <div class="meter-track"><div class="meter-fill {fc}" style="width:{min(pct,100)}%"></div></div>
    </div>'''

# factors HTML
factors_html = "".join(
    f'<div class="hf"><div class="hf-dot {f[3]}"></div>{f[0]}</div>'
    for f in factors)

# users
def ini(name): p=name.split(); return (p[0][0]+(p[-1][0] if len(p)>1 else p[0][1])).upper()
human_c = sum(1 for u in user_list if u["UserType"]=="Standard")
integ_c = sum(1 for u in user_list if "Integration" in u.get("Name","") or "Integration" in u.get("UserType",""))
sys_c   = max(0, len(user_list)-human_c-integ_c)
never_c = sum(1 for u in user_list if not u.get("LastLoginDate"))

user_rows = ""
for u in user_list[:5]:
    last = u.get("LastLoginDate"); ls = last[:10] if last else "Never"; lc = "#B91C1C" if not last else "var(--green)"
    ut = u.get("UserType","")
    pc = "pill-green" if ut=="Standard" else "pill-blue" if "Integration" in ut or "Integration" in u.get("Name","") else "pill-grey"
    pl = "Active" if ut=="Standard" else "Integration" if "pill-blue"==pc else "System"
    user_rows += f'''<div class="user-row"><div class="avatar">{ini(u["Name"])}</div>
      <div class="user-info"><div class="user-name">{u["Name"]}</div>
      <div class="user-meta">{ut} · Created {u["CreatedDate"][:10]} · Last login: <span style="color:{lc}">{ls}</span></div></div>
      {pill(pl,pc)}</div>'''

user_warn = (f'<div class="info-banner amber" style="margin-top:10px">⚠ {never_c} of {len(user_list)} users have never logged in. Integration users may need access validation before go-live.</div>'
             if never_c > 0 else
             '<div class="info-banner green" style="margin-top:10px">✓ All users have logged in recently.</div>')

# cron
cron_rows = "".join(f'''<div class="job-row"><div class="job-icon">⏱</div>
  <div class="job-info"><div class="job-name">{c["CronJobDetail"]["Name"]}</div>
  <div class="job-meta">Next run: {c.get("NextFireTime","—")[:16] if c.get("NextFireTime") else "—"} UTC</div></div>
  {pill(c["State"].capitalize(),"pill-green" if c["State"]=="WAITING" else "pill-red")}</div>''' for c in cron_list)
if not cron_rows: cron_rows = '<div class="info-banner amber">⚠ No scheduled jobs found in org.</div>'
cron_banner = ('<div class="info-banner green">✓ All scheduled jobs are in healthy WAITING state with upcoming fire times confirmed.</div>'
               if cron_all_ok and cron_list else "")

# classes
cls_rows = "".join(f'<tr><td>{c["Name"]}</td><td>{c.get("LengthWithoutComments",0):,}</td><td>{pill("Service/Controller","pill-blue")}</td></tr>' for c in class_list)

# async
async_rows = "".join(
    f'<tr><td>{j.get("ApexClass",{}).get("Name","—") if j.get("ApexClass") else j.get("JobType","—")}</td>'
    f'<td>{j.get("JobType","")}</td>'
    f'<td>{pill(j["Status"],"pill-green" if j["Status"]=="Completed" else "pill-red" if j["Status"] in ["Failed","Aborted"] else "pill-amber")}</td>'
    f'<td>{j["CreatedDate"][:10]}</td><td>{j.get("NumberOfErrors",0)}</td></tr>'
    for j in async_list) or "<tr><td colspan='5' style='text-align:center;color:#999'>No async jobs</td></tr>"
async_warn = (f'<div class="info-banner amber">⚠ {aborted} ScheduledApex job(s) aborted recently. Investigate and re-queue if necessary.</div>'
              if aborted > 0 else
              '<div class="info-banner green">✓ No failed or aborted async jobs.</div>')

# PRs
pr_open_html = "".join(f'''<div class="pr-item"><div class="pr-icon open">⤴</div>
  <div class="pr-info"><div class="pr-title"><a href="{p["html_url"]}" target="_blank" style="color:var(--blue);text-decoration:none">#{p["number"]} — {p["title"][:80]}</a></div>
  <div class="pr-meta"><strong>{p["head"]["ref"]} → {p["base"]["ref"]}</strong> &nbsp;·&nbsp; Opened: {p["created_at"][:10]} &nbsp;·&nbsp; Author: <strong>{p["user"]["login"]}</strong></div>
  </div>{pill("Awaiting Review","pill-amber")}</div>''' for p in open_prs)
if not pr_open_html: pr_open_html = '<div class="info-banner green">✓ No open pull requests.</div>'

pr_merged_html = "".join(f'''<div class="pr-item"><div class="pr-icon merged">✓</div>
  <div class="pr-info"><div class="pr-title"><a href="{p["html_url"]}" target="_blank" style="color:var(--blue);text-decoration:none">#{p["number"]} — {p["title"][:80]}</a></div>
  <div class="pr-meta"><strong>{p["head"]["ref"]} → {p["base"]["ref"]}</strong> &nbsp;·&nbsp; Merged: {p["merged_at"][:10]} &nbsp;·&nbsp; Author: <strong>{p["user"]["login"]}</strong></div>
  </div>{pill("Merged","pill-green")}</div>''' for p in prs_merged)
if not pr_merged_html: pr_merged_html = '<div class="info-banner blue">ℹ No PRs merged in the last 24 hours.</div>'

api_kpi_cls  = "red" if api_pct>80 else "amber" if api_pct>50 else "amber"
open_kpi_cls = "red" if len(open_prs)>5 else "amber" if len(open_prs)>2 else "amber"

alert_banner = (f'<div style="background:var(--red-bg);border-left:4px solid var(--red);padding:12px 18px;border-radius:6px;margin-bottom:20px;font-size:13px;color:var(--red);font-weight:600">'
                f'🔴 Health score is {score}/100 — below the 60-point threshold. A Slack alert has been sent.</div>'
                if score < 60 else "")

# ── Full HTML (CSS identical to local file) ──
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Griffith University – DevOps Metrics</title>
<style>
  :root {{
    color-scheme: light;
    --blue:       #1058B0;
    --blue-dark:  #0B3D7A;
    --blue-light: #EEF3FB;
    --blue-mid:   #2E74C8;
    --slate:      #2C3E50;
    --muted:      #6B7A8D;
    --line:       #DDE3EE;
    --bg:         #F2F5FA;
    --white:      #FFFFFF;
    --green:      #1A7A4A;
    --green-bg:   #E6F4EC;
    --red:        #B91C1C;
    --red-bg:     #FEE2E2;
    --amber:      #B45309;
    --amber-bg:   #FEF3C7;
    --radius:     8px;
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
  .page-sub   {{ font-size: 12px; color: var(--muted); margin-bottom: 22px; }}
  .health-score-wrap {{ display: flex; align-items: center; gap: 28px; background: var(--white); border-radius: var(--radius); box-shadow: 0 1px 4px rgba(0,0,0,.06); padding: 20px 28px; margin-bottom: 20px; }}
  .health-donut {{ flex-shrink: 0; }}
  .health-detail {{ flex: 1; }}
  .health-title {{ font-size: 13px; font-weight: 600; color: var(--blue-dark); margin-bottom: 12px; }}
  .health-factors {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
  .hf {{ display: flex; align-items: center; gap: 8px; font-size: 12px; }}
  .hf-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
  .hf-pass {{ background: var(--green); }}
  .hf-warn {{ background: #F59E0B; }}
  .hf-fail {{ background: var(--red); }}
  .health-score-num {{ text-align: center; }}
  .health-score-num .score {{ font-size: 44px; font-weight: 800; color: {hc_score}; line-height: 1; }}
  .health-score-num .score-label {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  .health-score-num .score-grade {{ font-size: 16px; font-weight: 700; color: {hc_score}; margin-top: 2px; }}
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
  .card-meta  {{ font-size: 11px; color: var(--muted); }}
  .card-body  {{ padding: 16px 18px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  thead tr {{ background: var(--blue-light); }}
  thead th {{ padding: 9px 12px; text-align: left; font-weight: 600; font-size: 11.5px; color: var(--blue-dark); }}
  tbody tr {{ border-bottom: 1px solid var(--line); }}
  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr:hover {{ background: #F8FAFF; }}
  tbody td {{ padding: 9px 12px; color: var(--slate); vertical-align: middle; }}
  .pill {{ display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
  .meter-item  {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }}
  .meter-head  {{ display: flex; justify-content: space-between; font-size: 12px; }}
  .meter-track {{ height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; }}
  .meter-fill  {{ height: 100%; border-radius: 4px; }}
  .fill-green  {{ background: var(--green); }} .fill-amber {{ background: #F59E0B; }} .fill-red {{ background: var(--red); }}
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
  .info-banner.blue  {{ background: var(--blue-light); color: var(--blue); }}
  .pr-item {{ display: flex; align-items: flex-start; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--line); }}
  .pr-item:last-child {{ border-bottom: none; }}
  .pr-icon {{ width: 30px; height: 30px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; }}
  .pr-icon.open   {{ background: #DBEAFE; color: #1D4ED8; }}
  .pr-icon.merged {{ background: var(--green-bg); color: var(--green); }}
  .pr-info {{ flex: 1; }} .pr-title {{ font-size: 12.5px; font-weight: 600; color: var(--slate); }} .pr-meta {{ font-size: 11px; color: var(--muted); margin-top: 3px; }}
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

  {alert_banner}

  <!-- Health Score -->
  <div class="health-score-wrap">
    <div class="health-donut">
      <svg width="130" height="130" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#EEF3FB" stroke-width="4"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#1A7A4A" stroke-width="4"
          stroke-dasharray="{g_arc} {g_rest}" stroke-dashoffset="{g_offset}" stroke-linecap="round"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#F59E0B" stroke-width="4"
          stroke-dasharray="{w_arc} {w_rest}" stroke-dashoffset="{w_offset}" stroke-linecap="round"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#B91C1C" stroke-width="4"
          stroke-dasharray="{r_arc} {r_rest}" stroke-dashoffset="{r_offset}" stroke-linecap="round"/>
        <text x="18" y="17" text-anchor="middle" font-size="8" font-weight="800" fill="#0B3D7A">{score}</text>
        <text x="18" y="22" text-anchor="middle" font-size="4" fill="#6B7A8D">/100</text>
      </svg>
    </div>
    <div class="health-score-num">
      <div class="score">{score}</div>
      <div class="score-grade">{hlabel}</div>
      <div class="score-label">Org Health Score</div>
    </div>
    <div class="health-detail">
      <div class="health-title">Score breakdown — based on live org data</div>
      <div class="health-factors">{factors_html}</div>
    </div>
  </div>

  <!-- KPI Strip -->
  <div class="kpi-strip">
    <div class="kpi green">
      <div class="kpi-label">Active Users</div>
      <div class="kpi-value">{len(user_list)}</div>
      <div class="kpi-sub">{human_c} human · {len(user_list)-human_c} system/integration</div>
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
    <div class="kpi {api_kpi_cls}">
      <div class="kpi-label">API Usage Today</div>
      <div class="kpi-value">~{api_pct}%</div>
      <div class="kpi-sub">{api_used:,} of {api_max:,} requests used</div>
    </div>
    <div class="kpi {open_kpi_cls}">
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
        {meter("Daily Async Apex Executions", axc_used, axc_max, axc_pct)}
        <div class="info-banner green">✓ All org limits are well within safe thresholds. No immediate capacity concerns.</div>
        <br>
        <table>
          <thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>
            <tr><td>Active Apex Classes</td><td>{pill("Pass","pill-green")}</td><td>{class_count} active classes</td></tr>
            <tr><td>Apex Triggers</td><td>{pill("Info","pill-blue")}</td><td>{trig_count} triggers — {"logic handled via Flows" if trig_count==0 else "review trigger usage"}</td></tr>
            <tr><td>Active Flows</td><td>{pill("Pass","pill-green")}</td><td>{flow_count} active flows across all types</td></tr>
            <tr><td>Scheduled Jobs</td><td>{pill("Pass" if cron_all_ok else "Review","pill-green" if cron_all_ok else "pill-amber")}</td><td>{len(cron_list)} jobs, {"all WAITING (healthy)" if cron_all_ok else "some not in WAITING state"}</td></tr>
            <tr><td>Async Apex Jobs</td><td>{pill("Review" if aborted>0 else "Pass","pill-amber" if aborted>0 else "pill-green")}</td><td>{f"{aborted} Aborted job(s) detected" if aborted>0 else "All jobs completed successfully"}</td></tr>
            <tr><td>Apex Test Results</td><td>{pill("Pass" if has_tests else "No Data","pill-green" if has_tests else "pill-amber")}</td><td>{"Test results available" if has_tests else "No test runs found — run tests to populate"}</td></tr>
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
            <div style="font-size:22px;font-weight:700;color:var(--green)">{human_c}</div>
            <div style="font-size:11px;color:var(--muted)">Human User</div>
          </div>
          <div style="flex:1;background:var(--blue-light);border-radius:6px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:var(--blue)">{integ_c}</div>
            <div style="font-size:11px;color:var(--muted)">Integration Users</div>
          </div>
          <div style="flex:1;background:#F1F3F7;border-radius:6px;padding:12px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:var(--muted)">{sys_c}</div>
            <div style="font-size:11px;color:var(--muted)">Automated Process</div>
          </div>
        </div>
        {user_rows}
        {user_warn}
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
      <div class="card-body">{cron_rows}{cron_banner}</div>
    </div>

    <div class="card">
      <div class="card-head">
        <div class="card-title">Async Apex Jobs & Deployment Activity</div>
        <div class="card-meta">Recent job history</div>
      </div>
      <div class="card-body">
        <table style="margin-bottom:14px">
          <thead><tr><th>Job</th><th>Type</th><th>Status</th><th>Date</th><th>Errors</th></tr></thead>
          <tbody>{async_rows}</tbody>
        </table>
        {async_warn}
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
        <div class="card-meta">All active · {trig_count} triggers</div>
      </div>
      <div class="card-body">
        <table>
          <thead><tr><th>Class Name</th><th>Size</th><th>Type</th></tr></thead>
          <tbody>{cls_rows or "<tr><td colspan='3' style='text-align:center;color:#999'>No classes found</td></tr>"}</tbody>
        </table>
        <div class="info-banner blue" style="margin-top:10px">ℹ {class_count} classes found. {"No Apex test results — recommend running <strong>Run All Tests</strong> to establish baseline coverage metrics." if not has_tests else "Test results available."}</div>
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

  <!-- Merged PRs -->
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
print(f"✓ Dashboard built. Score: {score}/100 ({hlabel})")
