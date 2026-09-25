import os, json, requests, datetime

SF_INSTANCE   = os.environ["SF_INSTANCE_URL"].rstrip("/")
SF_CLIENT_ID  = os.environ["SF_CLIENT_ID"]
SF_CLIENT_SEC = os.environ["SF_CLIENT_SECRET"]
GH_TOKEN      = os.environ["GH_TOKEN"]
GH_REPO       = "Akshay-0502/Salesforce"

NOW_IST = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST")

def sf_auth():
    r = requests.post(f"{SF_INSTANCE}/services/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": SF_CLIENT_ID,
        "client_secret": SF_CLIENT_SEC
    })
    r.raise_for_status()
    return r.json()["access_token"]

def sf_query(token, soql):
    r = requests.get(f"{SF_INSTANCE}/services/data/v59.0/query",
        params={"q": soql},
        headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    return r.json()

def sf_limits(token):
    r = requests.get(f"{SF_INSTANCE}/services/data/v59.0/limits/",
        headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    return r.json()

def gh(path):
    r = requests.get(f"https://api.github.com/repos/{GH_REPO}/{path}",
        headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    )
    return r.json() if r.ok else {}

token   = sf_auth()
limits  = sf_limits(token)
users   = sf_query(token, "SELECT Id,Name,UserType,IsActive,CreatedDate,LastLoginDate FROM User WHERE IsActive=true ORDER BY LastLoginDate DESC NULLS LAST LIMIT 10")
classes = sf_query(token, "SELECT COUNT() FROM ApexClass WHERE Status='Active'")
flows   = sf_query(token, "SELECT COUNT() FROM FlowDefinitionView WHERE IsActive=true")
crons   = sf_query(token, "SELECT Id,CronJobDetail.Name,State,NextFireTime FROM CronTrigger WHERE State='WAITING' LIMIT 20")
async_j = sf_query(token, "SELECT Id,ApexClass.Name,Status,JobType,CreatedDate FROM AsyncApexJob ORDER BY CreatedDate DESC LIMIT 10")
tests   = sf_query(token, "SELECT Outcome,MethodName,ApexClass.Name FROM ApexTestResult ORDER BY TestTimestamp DESC LIMIT 10")

prs_open   = gh("pulls?state=open&per_page=20")
prs_closed = gh("pulls?state=closed&per_page=50&sort=updated&direction=desc")
cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
prs_merged = [p for p in (prs_closed if isinstance(prs_closed, list) else [])
              if p.get("merged_at") and datetime.datetime.strptime(p["merged_at"], "%Y-%m-%dT%H:%M:%SZ") > cutoff]

api_used_pct = data_used_pct = 0
if "DailyApiRequests" in limits:
    mx = limits["DailyApiRequests"]["Max"]
    api_used_pct = round((mx - limits["DailyApiRequests"]["Remaining"]) / mx * 100, 1) if mx else 0
if "DataStorageMB" in limits:
    mx = limits["DataStorageMB"]["Max"]
    data_used_pct = round((mx - limits["DataStorageMB"]["Remaining"]) / mx * 100, 1) if mx else 0

aborted     = sum(1 for j in (async_j.get("records") or []) if j.get("Status") == "Aborted")
has_tests   = (tests.get("totalSize") or 0) > 0
flow_count  = flows.get("totalSize") or 0
class_count = classes.get("totalSize") or 0
cron_all_ok = all(c["State"] == "WAITING" for c in (crons.get("records") or [])) if crons.get("records") else True

score, factors = 0, []
if api_used_pct < 10:  score += 20; factors.append(("API Limit < 10% used", 20, True))
else:                  factors.append((f"API Usage {api_used_pct}%", -10, False))
if data_used_pct < 50: score += 15; factors.append(("Data Storage < 50% used", 15, True))
else:                  factors.append((f"Data Storage {data_used_pct}% used", -10, False))
if flow_count > 0:     score += 15; factors.append((f"Active Flows ({flow_count})", 15, True))
if cron_all_ok:        score += 10; factors.append(("All Scheduled Jobs WAITING", 10, True))
if class_count > 0:    score += 10; factors.append((f"Active Apex Classes ({class_count})", 10, True))
if aborted == 0:       score += 8;  factors.append(("No Aborted Async Jobs", 8, True))
else:                  score -= 8;  factors.append((f"Aborted Async Jobs ({aborted})", -8, False))
if has_tests:          score += 12; factors.append(("Apex Test Results Present", 12, True))
else:                  score -= 12; factors.append(("No Apex Test Results", -12, False))
score = max(0, min(100, score))

health_label, health_color = ("Healthy","#1A7A4A") if score>=80 else ("Good","#B45309") if score>=60 else ("Needs Attention","#B91C1C")

def badge(text, cls):
    colors = {"green":("#E6F4EC","#1A7A4A"),"red":("#FEE2E2","#B91C1C"),"amber":("#FEF3C7","#B45309")}
    bg, fg = colors.get(cls, ("#F1F3F7","#6B7A8D"))
    return f'<span style="background:{bg};color:{fg};padding:2px 9px;border-radius:12px;font-size:11px;font-weight:600">{text}</span>'

def limit_row(label, key):
    if key not in limits: return ""
    mx = limits[key]["Max"]; used = mx - limits[key]["Remaining"]
    pct = round(used/mx*100,1) if mx else 0
    c = "#B91C1C" if pct>80 else "#B45309" if pct>50 else "#1A7A4A"
    return f'<tr><td>{label}</td><td style="width:55%"><div style="background:#E5E7EB;border-radius:4px;height:8px;overflow:hidden"><div style="background:{c};width:{pct}%;height:100%"></div></div></td><td style="text-align:right;color:{c};font-weight:600">{used:,} / {mx:,} ({pct}%)</td></tr>'

factor_rows = "".join(f'<tr><td>{f[0]}</td><td style="color:{"#1A7A4A" if f[2] else "#B91C1C"};font-weight:700">{"+" if f[2] else ""}{f[1]}</td></tr>' for f in factors)
user_rows   = "".join(f'<tr><td>{u["Name"]}</td><td>{u["UserType"]}</td><td>{badge("Active","green") if u["IsActive"] else badge("Inactive","red")}</td><td>{u["CreatedDate"][:10]}</td><td style="color:{"#B91C1C" if not u.get("LastLoginDate") else "#1A7A4A"}">{u["LastLoginDate"][:10] if u.get("LastLoginDate") else "Never"}</td></tr>' for u in (users.get("records") or []))
cron_rows   = "".join(f'<tr><td>{c["CronJobDetail"]["Name"]}</td><td>{badge(c["State"],"green" if c["State"]=="WAITING" else "red")}</td><td>{c.get("NextFireTime","—")[:16] if c.get("NextFireTime") else "—"}</td></tr>' for c in (crons.get("records") or []))
async_rows  = "".join(f'<tr><td>{j.get("ApexClass",{}).get("Name","—") if j.get("ApexClass") else j.get("JobType","—")}</td><td>{badge(j["Status"],"green" if j["Status"]=="Completed" else "red" if j["Status"] in ["Failed","Aborted"] else "amber")}</td><td>{j["JobType"]}</td><td>{j["CreatedDate"][:10]}</td></tr>' for j in (async_j.get("records") or []))
pr_open_rows   = "".join(f'<tr><td><a href="{p["html_url"]}" target="_blank">#{p["number"]}</a></td><td>{p["title"][:60]}</td><td>{p["base"]["ref"]}</td><td>{p["user"]["login"]}</td><td>{p["created_at"][:10]}</td></tr>' for p in (prs_open if isinstance(prs_open,list) else []))
pr_merged_rows = "".join(f'<tr><td><a href="{p["html_url"]}" target="_blank">#{p["number"]}</a></td><td>{p["title"][:60]}</td><td>{p["base"]["ref"]}</td><td>{p["user"]["login"]}</td><td>{p["merged_at"][:10]}</td></tr>' for p in prs_merged) or "<tr><td colspan='5' style='color:#999;text-align:center'>No PRs merged in last 24h</td></tr>"

html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Griffith University – DevOps Metrics</title>
<style>
:root{{color-scheme:light;--blue:#1058B0;--dark:#0B3D7A;--bg:#F2F5FA;--white:#fff;--line:#DDE3EE;--slate:#2C3E50;--muted:#6B7A8D;--r:8px}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--slate);font-size:13.5px}}
.topbar{{background:var(--dark);color:#fff;padding:0 28px;height:52px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.18)}}
.logo{{font-size:15px;font-weight:700;color:#fff}}.logo span{{color:#7BB8FF}}
.live{{font-size:11.5px;color:#8BB8E8}}.dot{{display:inline-block;width:7px;height:7px;background:#4ADE80;border-radius:50%;margin-right:5px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.wrap{{max-width:1300px;margin:0 auto;padding:24px 28px 40px}}
.ptitle{{font-size:20px;font-weight:700;color:var(--dark);margin-bottom:4px}}
.psub{{font-size:12px;color:var(--muted);margin-bottom:22px}}
.kpi-strip{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:22px}}
.kpi{{background:var(--white);border-radius:var(--r);padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-top:3px solid var(--blue)}}
.kpi-val{{font-size:26px;font-weight:700;color:var(--dark)}}.kpi-lbl{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:4px}}
.card{{background:var(--white);border-radius:var(--r);box-shadow:0 1px 4px rgba(0,0,0,.06);padding:20px 24px;margin-bottom:20px}}
.card-title{{font-size:14px;font-weight:700;color:var(--dark);margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid var(--line)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#EEF3FB;color:var(--dark);padding:9px 12px;text-align:left;font-weight:600;font-size:12px}}
td{{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:middle}}
tr:hover td{{background:#F8FAFF}}
.health-wrap{{display:flex;gap:28px;align-items:flex-start}}
.footer{{text-align:center;font-size:11px;color:#aaa;margin-top:30px;padding-top:14px;border-top:1px solid var(--line)}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}
</style></head><body>
<div class="topbar">
  <div class="logo">Griffith <span>University</span></div>
  <div class="live"><span class="dot"></span>Auto-refreshed by GitHub Actions · {NOW_IST}</div>
</div>
<div class="wrap">
  <div class="ptitle">DevOps Metrics</div>
  <div class="psub">Salesforce Org · trailsignup-b168ab0f0d1b03 &nbsp;|&nbsp; Repo: Akshay-0502/Salesforce &nbsp;|&nbsp; Powered by GitHub Actions</div>
  <div class="kpi-strip">
    <div class="kpi"><div class="kpi-val">{users.get("totalSize",0)}</div><div class="kpi-lbl">Active Users</div></div>
    <div class="kpi"><div class="kpi-val">{class_count}</div><div class="kpi-lbl">Apex Classes</div></div>
    <div class="kpi"><div class="kpi-val">{flow_count}</div><div class="kpi-lbl">Active Flows</div></div>
    <div class="kpi"><div class="kpi-val">{crons.get("totalSize",0)}</div><div class="kpi-lbl">Scheduled Jobs</div></div>
    <div class="kpi"><div class="kpi-val">{api_used_pct}%</div><div class="kpi-lbl">API Usage</div></div>
    <div class="kpi"><div class="kpi-val">{len(prs_open) if isinstance(prs_open,list) else 0}</div><div class="kpi-lbl">Open PRs</div></div>
  </div>
  <div class="card">
    <div class="card-title">Org Health Score</div>
    <div class="health-wrap">
      <div style="flex-shrink:0;text-align:center">
        <svg width="140" height="140" viewBox="0 0 140 140">
          <circle cx="70" cy="70" r="54" fill="none" stroke="#E5E7EB" stroke-width="16"/>
          <circle cx="70" cy="70" r="54" fill="none" stroke="{health_color}" stroke-width="16"
            stroke-dasharray="{round(339.3*score/100,1)} 339.3" stroke-linecap="round" transform="rotate(-90 70 70)"/>
          <text x="70" y="66" text-anchor="middle" font-size="26" font-weight="700" fill="{health_color}">{score}</text>
          <text x="70" y="83" text-anchor="middle" font-size="11" fill="#6B7A8D">/ 100</text>
        </svg>
        <div style="font-weight:700;color:{health_color};font-size:14px;margin-top:4px">{health_label}</div>
      </div>
      <div style="flex:1"><table><thead><tr><th>Factor</th><th>Impact</th></tr></thead><tbody>{factor_rows}</tbody></table></div>
    </div>
  </div>
  <div class="card"><div class="card-title">Org Limits</div>
    <table><thead><tr><th>Limit</th><th>Usage</th><th style="text-align:right">Detail</th></tr></thead><tbody>
      {limit_row("Daily API Requests","DailyApiRequests")}
      {limit_row("Data Storage (MB)","DataStorageMB")}
      {limit_row("File Storage (MB)","FileStorageMB")}
    </tbody></table></div>
  <div class="card"><div class="card-title">User Activity (Top 10)</div>
    <table><thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Created</th><th>Last Login</th></tr></thead>
    <tbody>{user_rows}</tbody></table></div>
  <div class="card"><div class="card-title">Scheduled Jobs</div>
    <table><thead><tr><th>Job Name</th><th>State</th><th>Next Fire</th></tr></thead>
    <tbody>{cron_rows or "<tr><td colspan='3' style='color:#999;text-align:center'>No scheduled jobs</td></tr>"}</tbody></table></div>
  <div class="card"><div class="card-title">Recent Async Jobs</div>
    <table><thead><tr><th>Class / Type</th><th>Status</th><th>Job Type</th><th>Created</th></tr></thead>
    <tbody>{async_rows or "<tr><td colspan='4' style='color:#999;text-align:center'>No async jobs</td></tr>"}</tbody></table></div>
  <div class="card"><div class="card-title">Open Pull Requests</div>
    <table><thead><tr><th>PR</th><th>Title</th><th>Target</th><th>Author</th><th>Opened</th></tr></thead>
    <tbody>{pr_open_rows or "<tr><td colspan='5' style='color:#999;text-align:center'>No open PRs</td></tr>"}</tbody></table></div>
  <div class="card"><div class="card-title">PRs Merged in Last 24h</div>
    <table><thead><tr><th>PR</th><th>Title</th><th>Target</th><th>Author</th><th>Merged</th></tr></thead>
    <tbody>{pr_merged_rows}</tbody></table></div>
  <div class="footer">Griffith University DevOps Metrics &nbsp;·&nbsp; Powered entirely by GitHub Actions &nbsp;·&nbsp; Last updated: {NOW_IST}</div>
</div></body></html>"""

with open("docs/index.html", "w") as f:
    f.write(html)
print("Dashboard built successfully.")
