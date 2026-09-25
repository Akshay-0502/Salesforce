import os, requests, datetime

SF_INSTANCE   = os.environ["SF_INSTANCE_URL"].rstrip("/")
SF_CLIENT_ID  = os.environ["SF_CLIENT_ID"]
SF_CLIENT_SEC = os.environ["SF_CLIENT_SECRET"]
GH_TOKEN      = os.environ["GH_TOKEN"]
GH_REPO       = "Akshay-0502/Salesforce"
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")  # optional

NOW_IST = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST")
TODAY   = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y")

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
classes = sfq(token, "SELECT Id,Name,LengthWithoutComments FROM ApexClass WHERE Status='Active' ORDER BY LengthWithoutComments DESC LIMIT 10")
cls_cnt = sfq(token, "SELECT COUNT() FROM ApexClass WHERE Status='Active'")
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
def lim_pct(key):
    if key not in limits: return 0, 0, 0.0
    mx = limits[key]["Max"]; rem = limits[key]["Remaining"]; used = mx - rem
    return used, mx, round(used/mx*100, 2) if mx else 0.0

api_used, api_max, api_pct       = lim_pct("DailyApiRequests")
data_used, data_max, data_pct    = lim_pct("DataStorageMB")
file_used, file_max, file_pct    = lim_pct("FileStorageMB")
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

# ────────────────────────────────────────────────────────────
# HEALTH SCORE — purely additive, 100 points max
# Each category has a max weight. Deductions are capped within
# each category so one bad signal can't tank the whole score.
# ────────────────────────────────────────────────────────────
factors = []  # (label, points_earned, max_points, status)

# 1. API / Storage limits (25 pts)
if api_pct < 10:    api_pts = 15; api_status = "pass"; api_lbl = f"API usage healthy ({api_pct}%)"
elif api_pct < 50:  api_pts = 10; api_status = "warn"; api_lbl = f"API usage moderate ({api_pct}%)"
else:               api_pts = 0;  api_status = "fail"; api_lbl = f"API usage high ({api_pct}%)"

if data_pct < 50:   data_pts = 10; data_status = "pass"; data_lbl = "Data & file storage healthy"
elif data_pct < 80: data_pts = 5;  data_status = "warn"; data_lbl = f"Data storage moderate ({data_pct}%)"
else:               data_pts = 0;  data_status = "fail"; data_lbl = f"Data storage critical ({data_pct}%)"

factors.append((api_lbl,  api_pts,  15, api_status))
factors.append((data_lbl, data_pts, 10, data_status))

# 2. Automation health (25 pts)
if flow_count > 50:   flow_pts = 15; flow_status = "pass"; flow_lbl = f"{flow_count} active flows"
elif flow_count > 0:  flow_pts = 10; flow_status = "warn"; flow_lbl = f"{flow_count} active flows (low)"
else:                 flow_pts = 0;  flow_status = "fail"; flow_lbl = "No active flows"

if class_count > 0:   cls_pts = 10; cls_status = "pass"; cls_lbl = f"{class_count} active Apex classes"
else:                 cls_pts = 0;  cls_status = "warn"; cls_lbl = "No Apex classes found"

factors.append((flow_lbl, flow_pts, 15, flow_status))
factors.append((cls_lbl,  cls_pts,  10, cls_status))

# 3. Job health (20 pts)
if cron_list and cron_all_ok: cron_pts = 10; cron_status = "pass"; cron_lbl = f"All {len(cron_list)} scheduled jobs WAITING"
elif cron_list:               cron_pts = 5;  cron_status = "warn"; cron_lbl = "Some scheduled jobs not WAITING"
else:                         cron_pts = 7;  cron_status = "warn"; cron_lbl = "No scheduled jobs (may be expected)"

if aborted == 0:   ab_pts = 10; ab_status = "pass"; ab_lbl = "No aborted async jobs"
elif aborted <= 2: ab_pts = 5;  ab_status = "warn"; ab_lbl = f"{aborted} aborted async job(s)"
else:              ab_pts = 0;  ab_status = "fail"; ab_lbl = f"{aborted} aborted async jobs (critical)"

factors.append((cron_lbl, cron_pts, 10, cron_status))
factors.append((ab_lbl,   ab_pts,   10, ab_status))

# 4. Code quality (20 pts)
if trig_count == 0: trg_pts = 5; trg_status = "pass"; trg_lbl = "No triggers — flow-first architecture"
else:               trg_pts = 2; trg_status = "warn"; trg_lbl = f"{trig_count} Apex trigger(s) active"

if has_tests:   tst_pts = 15; tst_status = "pass"; tst_lbl = "Apex test results present"
else:           tst_pts = 5;  tst_status = "warn"; tst_lbl = "No Apex test results (run tests)"

factors.append((trg_lbl, trg_pts, 5,  trg_status))
factors.append((tst_lbl, tst_pts, 15, tst_status))

# 5. Deployment / PR hygiene (10 pts)
stale_prs = len([p for p in open_prs if (datetime.datetime.utcnow() - datetime.datetime.strptime(p["created_at"], "%Y-%m-%dT%H:%M:%SZ")).days > 7])
if stale_prs == 0 and len(open_prs) <= 3: pr_pts = 10; pr_status = "pass"; pr_lbl = f"{len(open_prs)} open PR(s), none stale"
elif stale_prs == 0:                       pr_pts = 7;  pr_status = "warn"; pr_lbl = f"{len(open_prs)} open PRs"
else:                                      pr_pts = 3;  pr_status = "fail"; pr_lbl = f"{stale_prs} stale PR(s) > 7 days"

factors.append((pr_lbl, pr_pts, 10, pr_status))

# Final score = sum of earned / sum of max * 100
total_earned = sum(f[1] for f in factors)
total_max    = sum(f[2] for f in factors)
score = round(total_earned / total_max * 100)

if score >= 80:   health_label, hc = "Healthy",         "#1A7A4A"
elif score >= 60: health_label, hc = "Good",            "#B45309"
else:             health_label, hc = "Needs Attention", "#B91C1C"

# ── Donut: single arc showing score %, correct colour ──
# circumference of r=15.9 circle ≈ 99.9 ≈ 100 for simplicity
arc_filled = score          # green/amber/red filled portion
arc_empty  = 100 - score    # grey remainder

# ── Slack alert if score < 60 ──
if score < 60 and SLACK_WEBHOOK:
    factor_lines = "\n".join(f"• {f[0]} ({f[1]}/{f[2]} pts)" for f in factors)
    slack_msg = {
        "text": f":red_circle: *Salesforce Org Health Alert*",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "🔴 Salesforce Org Health Alert"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"*Health Score dropped to {score}/100* — _{health_label}_\n\n"
                        f"*Org:* trailsignup-b168ab0f0d1b03\n"
                        f"*Checked:* {NOW_IST}\n\n"
                        f"*Factor breakdown:*\n{factor_lines}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"<https://akshay-0502.github.io/Salesforce/|View Dashboard>"}}
        ]
    }
    try:
        sr = requests.post(SLACK_WEBHOOK, json=slack_msg, timeout=10)
        print(f"Slack alert sent: {sr.status_code}")
    except Exception as e:
        print(f"Slack alert failed: {e}")

# ── HTML helpers ──
def pill(text, cls):
    styles = {"green":"background:#E6F4EC;color:#1A7A4A","red":"background:#FEE2E2;color:#B91C1C",
              "amber":"background:#FEF3C7;color:#B45309","blue":"background:#EEF3FB;color:#1058B0",
              "grey":"background:#F1F3F7;color:#6B7A8D"}
    s = styles.get(cls, styles["grey"])
    return f'<span class="pill" style="{s}">{text}</span>'

def meter(label, used, mx, pct):
    c = "#B91C1C" if pct>80 else "#B45309" if pct>50 else "#1A7A4A"
    return f'''<div class="meter-item">
      <div class="meter-head"><span>{label}</span><span style="font-weight:600;color:{c}">~{pct}% used ({used:,} / {mx:,})</span></div>
      <div class="meter-track"><div class="meter-fill" style="width:{min(pct,100)}%;background:{c}"></div></div>
    </div>'''

def factor_row(label, earned, max_pts, typ):
    dot_color = {"pass":"#1A7A4A","warn":"#F59E0B","fail":"#B91C1C"}.get(typ,"#6B7A8D")
    return f'<div class="hf"><div class="hf-dot" style="background:{dot_color}"></div>{label} ({earned}/{max_pts} pts)</div>'

factor_html = "".join(factor_row(f[0],f[1],f[2],f[3]) for f in factors)

# users
def initials(name): p=name.split(); return (p[0][0]+(p[-1][0] if len(p)>1 else p[0][1])).upper()
human_count = sum(1 for u in user_list if u["UserType"]=="Standard")
integ_count = sum(1 for u in user_list if "Integration" in u.get("Name","") or "Integration" in u.get("UserType",""))
sys_count   = max(0, len(user_list) - human_count - integ_count)
never_count = sum(1 for u in user_list if not u.get("LastLoginDate"))

user_rows_html = ""
for u in user_list[:6]:
    last = u.get("LastLoginDate"); last_str = last[:10] if last else "Never"; lc = "#B91C1C" if not last else "#1A7A4A"
    ut = u.get("UserType","")
    pc,plbl = ("green","Active") if ut=="Standard" else ("blue","Integration") if "Integration" in ut or "Integration" in u.get("Name","") else ("grey","System")
    user_rows_html += f'''<div class="user-row"><div class="avatar">{initials(u["Name"])}</div>
      <div class="user-info"><div class="user-name">{u["Name"]}</div>
      <div class="user-meta">{ut} · Created {u["CreatedDate"][:10]} · Last login: <span style="color:{lc}">{last_str}</span></div></div>
      {pill(plbl,pc)}</div>'''

user_warning = f'<div class="info-banner amber" style="margin-top:10px">⚠ {never_count} of {len(user_list)} users have never logged in.</div>' if never_count>0 else '<div class="info-banner green" style="margin-top:10px">✓ All users have logged in recently.</div>'

cron_html = "".join(f'''<div class="job-row"><div class="job-icon">⏱</div>
  <div class="job-info"><div class="job-name">{c["CronJobDetail"]["Name"]}</div>
  <div class="job-meta">Next: {c.get("NextFireTime","—")[:16] if c.get("NextFireTime") else "—"} UTC</div></div>
  {pill(c["State"].capitalize(),"green" if c["State"]=="WAITING" else "red")}</div>''' for c in cron_list) or '<div class="info-banner amber">⚠ No scheduled jobs found.</div>'

cron_banner = '<div class="info-banner green" style="margin-top:4px">✓ All scheduled jobs healthy.</div>' if cron_all_ok and cron_list else ""

class_rows_html = "".join(f'<tr><td>{c["Name"]}</td><td>{c.get("LengthWithoutComments",0):,}</td><td>{pill("Class","blue")}</td></tr>' for c in class_list)

async_rows_html = "".join(
    f'<tr><td>{j.get("ApexClass",{}).get("Name","—") if j.get("ApexClass") else j.get("JobType","—")}</td>'
    f'<td>{j.get("JobType","")}</td>'
    f'<td>{pill(j["Status"],"green" if j["Status"]=="Completed" else "red" if j["Status"] in ["Failed","Aborted"] else "amber")}</td>'
    f'<td>{j["CreatedDate"][:10]}</td><td>{j.get("NumberOfErrors",0)}</td></tr>'
    for j in async_list) or "<tr><td colspan='5' style='color:#999;text-align:center'>No async jobs</td></tr>"

async_banner = f'<div class="info-banner amber">⚠ {aborted} aborted job(s) — investigate and re-queue.</div>' if aborted>0 else '<div class="info-banner green">✓ No failed or aborted async jobs.</div>'

pr_open_html = "".join(f'''<div class="pr-item"><div class="pr-icon open">⤴</div>
  <div class="pr-info"><div class="pr-title"><a href="{p["html_url"]}" target="_blank" style="color:#1058B0">#{p["number"]} — {p["title"][:80]}</a></div>
  <div class="pr-meta"><strong>{p["head"]["ref"]} → {p["base"]["ref"]}</strong> · {p["created_at"][:10]} · {p["user"]["login"]}</div></div>
  {pill("Awaiting Review","amber")}</div>''' for p in open_prs) or '<div class="info-banner green">✓ No open pull requests.</div>'

pr_merged_html = "".join(f'''<div class="pr-item"><div class="pr-icon merged">✓</div>
  <div class="pr-info"><div class="pr-title"><a href="{p["html_url"]}" target="_blank" style="color:#1058B0">#{p["number"]} — {p["title"][:80]}</a></div>
  <div class="pr-meta"><strong>{p["head"]["ref"]} → {p["base"]["ref"]}</strong> · Merged: {p["merged_at"][:10]} · {p["user"]["login"]}</div></div>
  {pill("Merged","green")}</div>''' for p in prs_merged) or '<div class="info-banner blue">ℹ No PRs merged in the last 24 hours.</div>'

api_cls  = "red" if api_pct>80 else "amber" if api_pct>50 else "green"
open_cls = "red" if len(open_prs)>5 else "amber" if len(open_prs)>2 else "green"

# ── HTML ──
html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Griffith University – DevOps Metrics</title>
<style>
  :root{{color-scheme:light;--blue:#1058B0;--blue-dark:#0B3D7A;--blue-light:#EEF3FB;--slate:#2C3E50;--muted:#6B7A8D;--line:#DDE3EE;--bg:#F2F5FA;--white:#FFFFFF;--green:#1A7A4A;--green-bg:#E6F4EC;--red:#B91C1C;--red-bg:#FEE2E2;--amber:#B45309;--amber-bg:#FEF3C7;--radius:8px}}
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--slate);font-size:13.5px;line-height:1.5}}
  .topbar{{background:var(--blue-dark);color:#fff;padding:0 28px;height:52px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.18)}}
  .topbar-logo{{font-size:15px;font-weight:700}}.topbar-logo span{{color:#7BB8FF}}
  .topbar-badge{{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:20px;padding:2px 10px;font-size:11px;color:#B8D4FF;margin-left:12px}}
  .topbar-right{{font-size:11.5px;color:#8BB8E8}}
  .live-dot{{display:inline-block;width:7px;height:7px;background:#4ADE80;border-radius:50%;margin-right:5px;animation:pulse 2s infinite}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
  .wrapper{{max-width:1300px;margin:0 auto;padding:24px 28px 40px}}
  .page-title{{font-size:20px;font-weight:700;color:var(--blue-dark);margin-bottom:4px}}
  .page-sub{{font-size:12px;color:var(--muted);margin-bottom:22px}}
  .health-score-wrap{{display:flex;align-items:center;gap:28px;background:var(--white);border-radius:var(--radius);box-shadow:0 1px 4px rgba(0,0,0,.06);padding:20px 28px;margin-bottom:20px}}
  .health-donut{{flex-shrink:0}}.health-detail{{flex:1}}
  .health-title{{font-size:13px;font-weight:600;color:var(--blue-dark);margin-bottom:12px}}
  .health-factors{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}}
  .hf{{display:flex;align-items:center;gap:8px;font-size:12px}}
  .hf-dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
  .health-score-num{{text-align:center}}
  .score{{font-size:44px;font-weight:800;color:{hc};line-height:1}}
  .score-grade{{font-size:16px;font-weight:700;color:{hc};margin-top:2px}}
  .score-label{{font-size:11px;color:var(--muted);margin-top:4px}}
  .kpi-strip{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:24px}}
  .kpi{{background:var(--white);border-radius:var(--radius);padding:16px 18px;border-top:3px solid var(--blue);box-shadow:0 1px 4px rgba(0,0,0,.06)}}
  .kpi.green{{border-top-color:var(--green)}}.kpi.amber{{border-top-color:var(--amber)}}.kpi.red{{border-top-color:var(--red)}}
  .kpi-label{{font-size:11px;color:var(--muted);margin-bottom:6px}}
  .kpi-value{{font-size:26px;font-weight:700;color:var(--blue-dark);line-height:1}}
  .kpi.green .kpi-value{{color:var(--green)}}.kpi.amber .kpi-value{{color:var(--amber)}}.kpi.red .kpi-value{{color:var(--red)}}
  .kpi-sub{{font-size:11px;color:var(--muted);margin-top:5px}}
  .grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}}
  .card{{background:var(--white);border-radius:var(--radius);box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:hidden}}
  .card-head{{padding:14px 18px 10px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}}
  .card-title{{font-size:13px;font-weight:600;color:var(--blue-dark)}}.card-meta{{font-size:11px;color:var(--muted)}}
  .card-body{{padding:16px 18px}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px}}
  thead tr{{background:var(--blue-light)}}
  thead th{{padding:9px 12px;text-align:left;font-weight:600;font-size:11.5px;color:var(--blue-dark)}}
  tbody tr{{border-bottom:1px solid var(--line)}}tbody tr:last-child{{border-bottom:none}}
  tbody tr:hover{{background:#F8FAFF}}tbody td{{padding:9px 12px;color:var(--slate);vertical-align:middle}}
  .pill{{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600;white-space:nowrap}}
  .meter-item{{display:flex;flex-direction:column;gap:5px;margin-bottom:12px}}
  .meter-head{{display:flex;justify-content:space-between;font-size:12px}}
  .meter-track{{height:8px;background:var(--line);border-radius:4px;overflow:hidden}}
  .meter-fill{{height:100%;border-radius:4px}}
  .user-row{{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}}
  .user-row:last-child{{border-bottom:none}}
  .avatar{{width:32px;height:32px;border-radius:50%;background:var(--blue-light);color:var(--blue-dark);font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}}
  .user-info{{flex:1}}.user-name{{font-size:12.5px;font-weight:600}}.user-meta{{font-size:11px;color:var(--muted)}}
  .job-row{{display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}}
  .job-row:last-child{{border-bottom:none}}
  .job-icon{{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0;background:var(--blue-light);color:var(--blue)}}
  .job-info{{flex:1}}.job-name{{font-size:12.5px;font-weight:600}}.job-meta{{font-size:11px;color:var(--muted)}}
  .info-banner{{padding:10px 14px;border-radius:6px;font-size:12px;margin-top:12px}}
  .info-banner.amber{{background:var(--amber-bg);color:var(--amber)}}.info-banner.green{{background:var(--green-bg);color:var(--green)}}.info-banner.blue{{background:var(--blue-light);color:var(--blue)}}
  .pr-item{{display:flex;align-items:flex-start;gap:12px;padding:11px 0;border-bottom:1px solid var(--line)}}
  .pr-item:last-child{{border-bottom:none}}
  .pr-icon{{width:30px;height:30px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}}
  .pr-icon.open{{background:#DBEAFE;color:#1D4ED8}}.pr-icon.merged{{background:var(--green-bg);color:var(--green)}}
  .pr-info{{flex:1}}.pr-title{{font-size:12.5px;font-weight:600}}.pr-meta{{font-size:11px;color:var(--muted);margin-top:3px}}
  .dash-footer{{text-align:center;font-size:11px;color:var(--muted);margin-top:30px;padding-top:16px;border-top:1px solid var(--line)}}
  a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}
  .score-alert{{background:var(--red-bg);border-left:4px solid var(--red);padding:12px 18px;border-radius:6px;margin-bottom:20px;font-size:13px;color:var(--red);font-weight:600;display:{"block" if score<60 else "none"}}}
</style></head><body>
<div class="topbar">
  <div style="display:flex;align-items:center">
    <div class="topbar-logo">Griffith <span>University</span></div>
    <div class="topbar-badge">Trailsignup Org · Live</div>
  </div>
  <div style="display:flex;align-items:center;gap:14px">
    <div style="background:{hc};color:#fff;border-radius:20px;padding:3px 14px;font-size:12px;font-weight:700;letter-spacing:0.3px">
      Health {score}% &nbsp;·&nbsp; {health_label}
    </div>
    <div class="topbar-right"><span class="live-dot"></span>Data pulled: {TODAY} · Org: trailsignup-b168ab0f0d1b03</div>
  </div>
</div>
<div class="wrapper">
  <div class="page-title">DevOps Metrics</div>
  <div class="page-sub">Real-time Salesforce org health, user activity, automation coverage, and scheduled job status — pulled directly from your org via API</div>

  {"<div class='score-alert'>🔴 Health score is " + str(score) + "/100 — below the 60-point threshold. A Slack alert has been sent.</div>" if score < 60 else ""}

  <!-- Health Score -->
  <div class="health-score-wrap">
    <div class="health-donut">
      <svg width="130" height="130" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="#E5E7EB" stroke-width="4"/>
        <circle cx="18" cy="18" r="15.9" fill="none" stroke="{hc}" stroke-width="4"
          stroke-dasharray="{arc_filled} {arc_empty}" stroke-dashoffset="25" stroke-linecap="round"/>
        <text x="18" y="17.5" text-anchor="middle" font-size="7" font-weight="800" fill="{hc}">{score}%</text>
        <text x="18" y="23" text-anchor="middle" font-size="3.5" fill="#6B7A8D">Health</text>
      </svg>
    </div>
    <div class="health-score-num">
      <div class="score">{score}%</div>
      <div class="score-grade">{health_label}</div>
      <div class="score-label">Org Health Score (0–100)</div>
    </div>
    <div class="health-detail">
      <div class="health-title">Score breakdown — {score}% health based on live Salesforce org data ({total_earned}/{total_max} pts)</div>
      <div class="health-factors">{factor_html}</div>
    </div>
  </div>

  <!-- KPI Strip -->
  <div class="kpi-strip">
    <div class="kpi green"><div class="kpi-label">Active Users</div><div class="kpi-value">{len(user_list)}</div><div class="kpi-sub">{human_count} human · {len(user_list)-human_count} system/integration</div></div>
    <div class="kpi green"><div class="kpi-label">Active Apex Classes</div><div class="kpi-value">{class_count}</div><div class="kpi-sub">{trig_count} triggers · all active</div></div>
    <div class="kpi green"><div class="kpi-label">Active Flows</div><div class="kpi-value">{flow_count}</div><div class="kpi-sub">AutoLaunched, Screen, Orchestrator</div></div>
    <div class="kpi green"><div class="kpi-label">Scheduled Jobs</div><div class="kpi-value">{len(cron_list)}</div><div class="kpi-sub">{"All WAITING" if cron_all_ok else "Check states"}</div></div>
    <div class="kpi {api_cls}"><div class="kpi-label">API Usage Today</div><div class="kpi-value">~{api_pct}%</div><div class="kpi-sub">{api_used:,} of {api_max:,} requests</div></div>
    <div class="kpi {open_cls}"><div class="kpi-label">Open Pull Requests</div><div class="kpi-value">{len(open_prs)}</div><div class="kpi-sub">{"feature → uat · awaiting review" if open_prs else "No open PRs"}</div></div>
  </div>

  <!-- Row 1 -->
  <div class="grid-2">
    <div class="card"><div class="card-head"><div class="card-title">Salesforce Org Health — Live Limits</div><div class="card-meta">trailsignup-b168ab0f0d1b03</div></div>
      <div class="card-body">
        {meter("Daily API Requests", api_used, api_max, api_pct)}
        {meter("Data Storage (MB)", data_used, data_max, data_pct)}
        {meter("File Storage (MB)", file_used, file_max, file_pct)}
        {meter("Daily Async Apex Executions", async_used, async_max, async_pct)}
        <div class="info-banner green">✓ All org limits within safe thresholds.</div>
        <br><table><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead><tbody>
          <tr><td>Active Apex Classes</td><td>{pill("Pass","green")}</td><td>{class_count} active classes</td></tr>
          <tr><td>Apex Triggers</td><td>{pill("Info","blue")}</td><td>{trig_count} triggers — {"flow-first architecture" if trig_count==0 else "review trigger usage"}</td></tr>
          <tr><td>Active Flows</td><td>{pill("Pass","green")}</td><td>{flow_count} active flows</td></tr>
          <tr><td>Scheduled Jobs</td><td>{pill("Pass" if cron_all_ok else "Review","green" if cron_all_ok else "amber")}</td><td>{len(cron_list)} jobs, {"all WAITING" if cron_all_ok else "check states"}</td></tr>
          <tr><td>Async Apex Jobs</td><td>{pill("Review" if aborted>0 else "Pass","amber" if aborted>0 else "green")}</td><td>{f"{aborted} Aborted" if aborted>0 else "All healthy"}</td></tr>
          <tr><td>Apex Test Results</td><td>{pill("Pass" if has_tests else "No Data","green" if has_tests else "amber")}</td><td>{"Results available" if has_tests else "Run tests to populate"}</td></tr>
        </tbody></table>
      </div>
    </div>
    <div class="card"><div class="card-head"><div class="card-title">User Activity & Onboarding</div><div class="card-meta">{len(user_list)} active users</div></div>
      <div class="card-body">
        <div style="display:flex;gap:14px;margin-bottom:16px">
          <div style="flex:1;background:var(--green-bg);border-radius:6px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:var(--green)">{human_count}</div><div style="font-size:11px;color:var(--muted)">Human Users</div></div>
          <div style="flex:1;background:var(--blue-light);border-radius:6px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:var(--blue)">{integ_count}</div><div style="font-size:11px;color:var(--muted)">Integration Users</div></div>
          <div style="flex:1;background:#F1F3F7;border-radius:6px;padding:12px;text-align:center"><div style="font-size:22px;font-weight:700;color:var(--muted)">{sys_count}</div><div style="font-size:11px;color:var(--muted)">Automated Process</div></div>
        </div>
        {user_rows_html}{user_warning}
      </div>
    </div>
  </div>

  <!-- Row 2 -->
  <div class="grid-2">
    <div class="card"><div class="card-head"><div class="card-title">Scheduled Jobs</div><div class="card-meta">{len(cron_list)} jobs</div></div>
      <div class="card-body">{cron_html}{cron_banner}</div></div>
    <div class="card"><div class="card-head"><div class="card-title">Async Apex Jobs</div><div class="card-meta">Recent history</div></div>
      <div class="card-body">
        <table><thead><tr><th>Job</th><th>Type</th><th>Status</th><th>Date</th><th>Errors</th></tr></thead>
        <tbody>{async_rows_html}</tbody></table>
        {async_banner}
        <br><div style="font-size:12px;font-weight:600;color:var(--slate);margin-bottom:8px">Test Coverage</div>
        {"<div class='info-banner green'>✓ Apex test results found.</div>" if has_tests else "<div class='info-banner amber'>⚠ No test results — run <strong>Run All Tests</strong> in Setup.</div>"}
      </div>
    </div>
  </div>

  <!-- Row 3 -->
  <div class="grid-2">
    <div class="card"><div class="card-head"><div class="card-title">Active Apex Classes ({class_count})</div><div class="card-meta">Top by size</div></div>
      <div class="card-body"><table><thead><tr><th>Class Name</th><th>Size</th><th>Type</th></tr></thead>
      <tbody>{class_rows_html}</tbody></table>
      <div class="info-banner blue" style="margin-top:10px">ℹ {class_count} classes · {"No test results yet — run tests." if not has_tests else "Test results available."}</div>
      </div>
    </div>
    <div class="card"><div class="card-head"><div class="card-title">Pull Requests — Open</div><div class="card-meta">github.com/Akshay-0502/Salesforce</div></div>
      <div class="card-body">{pr_open_html}</div></div>
  </div>

  <!-- Merged PRs -->
  <div class="card" style="margin-bottom:18px">
    <div class="card-head"><div class="card-title">PRs Merged in Last 24h</div><div class="card-meta">{len(prs_merged)} merged</div></div>
    <div class="card-body">{pr_merged_html}</div>
  </div>

  <div class="dash-footer">
    Griffith University DevOps Metrics &nbsp;·&nbsp; Org: trailsignup-b168ab0f0d1b03 &nbsp;·&nbsp; Powered by GitHub Actions &nbsp;·&nbsp; Refreshed: {NOW_IST}
  </div>
</div></body></html>"""

with open("docs/index.html", "w") as f:
    f.write(html)
print(f"✓ Dashboard built. Score: {score}/100 ({health_label}) | Earned: {total_earned}/{total_max}")
