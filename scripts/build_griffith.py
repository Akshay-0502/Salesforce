"""
build_griffith.py
=================
Generates griffith_dashboard.html with:
  - Live Salesforce SIT Sandbox data
  - ADO pull requests targeting the `sit` branch (baked in, no browser auth)

Environment variables (set in ADO Variable Group: salesforce-devops-secrets):
  SF_INSTANCE_URL     e.g. https://griffith--sit.sandbox.my.salesforce.com
  SF_CLIENT_ID        Connected App consumer key
  SF_CLIENT_SECRET    Connected App consumer secret
  ADO_ORG             e.g. griffith-SalesforceCRM
  ADO_PROJECT         e.g. RSDF-SalesforcePlatform
  ADO_REPO            e.g. RSDF-SalesforcePlatform
  ADO_PAT             Personal Access Token with Code (Read) scope
  ADO_SIT_BRANCH      target branch, default: sit
"""

import os, sys, json, base64, datetime, re
import requests

# ── Config ────────────────────────────────────────────────────────────────────
SF_INSTANCE  = os.environ.get("SF_INSTANCE_URL", "").rstrip("/")
SF_CLIENT_ID = os.environ.get("SF_CLIENT_ID", "")
SF_CLIENT_SEC= os.environ.get("SF_CLIENT_SECRET", "")

ADO_ORG     = os.environ.get("ADO_ORG",     "griffith-SalesforceCRM")
ADO_PROJECT = os.environ.get("ADO_PROJECT", "RSDF-SalesforcePlatform")
ADO_REPO    = os.environ.get("ADO_REPO",    "RSDF-SalesforcePlatform")
ADO_PAT     = os.environ.get("ADO_PAT",     "")
SIT_BRANCH  = os.environ.get("ADO_SIT_BRANCH", "sit")

NOW_IST = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
           ).strftime("%d %b %Y, %H:%M IST")

# ── Salesforce helpers ────────────────────────────────────────────────────────
def sf_token():
    r = requests.post(f"{SF_INSTANCE}/services/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": SF_CLIENT_ID,
        "client_secret": SF_CLIENT_SEC
    })
    r.raise_for_status()
    return r.json()["access_token"]

def q(token, soql):
    r = requests.get(f"{SF_INSTANCE}/services/data/v59.0/query",
        params={"q": soql},
        headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()

def limits(token):
    r = requests.get(f"{SF_INSTANCE}/services/data/v59.0/limits/",
        headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()

def lp(lims, key):
    if key not in lims: return 0, 0, 0.0
    mx = lims[key]["Max"]; used = mx - lims[key]["Remaining"]
    return used, mx, round(used/mx*100, 2) if mx else 0.0

# ── ADO PR helper ─────────────────────────────────────────────────────────────
def fetch_ado_prs():
    """Fetch PRs for all three statuses from ADO. Returns dict or None on error."""
    if not ADO_PAT:
        print("  [ADO] ADO_PAT not set — PR section will show empty state")
        return None

    b64 = base64.b64encode(f":{ADO_PAT}".encode()).decode()
    headers = {
        "Authorization": f"Basic {b64}",
        "Content-Type": "application/json"
    }
    base_url = (
        f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_apis/git/repositories"
        f"/{ADO_REPO}/pullrequests"
    )
    target_ref = f"refs/heads/{SIT_BRANCH}"
    result = {"generated_at": datetime.datetime.utcnow().isoformat() + "Z"}

    for status in ("active", "completed", "abandoned"):
        try:
            r = requests.get(base_url, params={
                "searchCriteria.targetRefName": target_ref,
                "searchCriteria.status": status,
                "$top": 100,
                "api-version": "7.1"
            }, headers=headers, timeout=15)
            if r.ok:
                result[status] = r.json().get("value", [])
                print(f"  [ADO] {status}: {len(result[status])} PR(s)")
            else:
                print(f"  [ADO] {status}: HTTP {r.status_code} — {r.text[:120]}")
                result[status] = []
        except Exception as e:
            print(f"  [ADO] {status}: error — {e}")
            result[status] = []

    return result

# ── Salesforce data fetch ──────────────────────────────────────────────────────
def fetch_sf_data():
    print("Authenticating with Salesforce…")
    token = sf_token()
    print("  ✓ authenticated")

    lims = limits(token)
    api_used, api_max, api_pct   = lp(lims, "DailyApiRequests")
    data_used, data_max, data_pct = lp(lims, "DataStorageMB")
    file_used, file_max, file_pct = lp(lims, "FileStorageMB")

    user_count  = q(token, "SELECT COUNT() FROM User WHERE IsActive=true")["totalSize"]
    flow_count  = q(token, "SELECT COUNT() FROM FlowDefinitionView WHERE IsActive=true")["totalSize"]
    class_count = q(token, "SELECT COUNT() FROM ApexClass WHERE Status='Active'")["totalSize"]
    trig_count  = q(token, "SELECT COUNT() FROM ApexTrigger WHERE Status='Active'")["totalSize"]
    cron_count  = q(token, "SELECT COUNT() FROM CronTrigger WHERE State='WAITING'")["totalSize"]

    # Detail rows (display only — counts above are accurate)
    users   = q(token, "SELECT Name,UserType,LastLoginDate FROM User WHERE IsActive=true ORDER BY LastLoginDate DESC NULLS LAST LIMIT 20")["records"]
    classes = q(token, "SELECT Name,LengthWithoutComments FROM ApexClass WHERE Status='Active' ORDER BY LengthWithoutComments DESC LIMIT 10")["records"]
    crons   = q(token, "SELECT CronJobDetail.Name,State,NextFireTime FROM CronTrigger ORDER BY NextFireTime ASC LIMIT 20")["records"]
    flows   = q(token, "SELECT Label,ProcessType FROM FlowDefinitionView WHERE IsActive=true ORDER BY Label ASC LIMIT 20")["records"]

    # Security signals
    mad_users = q(token, "SELECT COUNT() FROM User WHERE IsActive=true AND Profile.PermissionsModifyAllData=true")["totalSize"]
    mad_psets = q(token, "SELECT Name FROM PermissionSet WHERE PermissionsModifyAllData=true AND IsOwnedByProfile=false")["records"]
    guest_cnt = q(token, "SELECT COUNT() FROM User WHERE IsActive=true AND UserType IN ('Guest','CSPLitePortal','PowerCustomerSuccess','PowerPartner')")["totalSize"]
    api_users = q(token, "SELECT COUNT() FROM User WHERE IsActive=true AND Profile.PermissionsApiEnabled=true")["totalSize"]

    return {
        "user_count": user_count, "flow_count": flow_count,
        "class_count": class_count, "trig_count": trig_count, "cron_count": cron_count,
        "api_used": api_used, "api_max": api_max, "api_pct": api_pct,
        "data_used": data_used, "data_max": data_max, "data_pct": data_pct,
        "file_used": file_used, "file_max": file_max, "file_pct": file_pct,
        "users": users, "classes": classes, "crons": crons, "flows": flows,
        "mad_users": mad_users, "mad_psets": mad_psets,
        "guest_cnt": guest_cnt, "api_users": api_users,
    }

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # 1. Fetch Salesforce data
    sf = fetch_sf_data()
    print(f"  users={sf['user_count']}, flows={sf['flow_count']}, classes={sf['class_count']}, crons={sf['cron_count']}")

    # 2. Fetch ADO PRs
    print("Fetching ADO pull requests…")
    pr_data = fetch_ado_prs()

    # 3. Load HTML template
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "../docs/griffith_dashboard.html")
    if not os.path.exists(template_path):
        print(f"ERROR: Template not found at {template_path}")
        sys.exit(1)

    with open(template_path, "r") as f:
        html = f.read()

    # 4. Bake PR data into HTML
    pr_json = json.dumps(pr_data) if pr_data else "null"
    html = html.replace("PR_DATA_PLACEHOLDER", pr_json)

    # 5. Update timestamp in topbar
    html = re.sub(
        r'Data Last Pulled:.*?</strong>',
        f'Data Last Pulled: &nbsp;<strong>{NOW_IST}</strong>',
        html
    )

    # 6. Write output
    out_path = os.path.join(script_dir, "../docs/griffith_dashboard.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"✅ Griffith dashboard written to {out_path}")
    total_prs = 0
    if pr_data:
        total_prs = len(pr_data.get("active",[])) + len(pr_data.get("completed",[])) + len(pr_data.get("abandoned",[]))
    print(f"   PRs baked in: {total_prs} (active={len(pr_data.get('active',[]) if pr_data else [])}, completed={len(pr_data.get('completed',[]) if pr_data else [])}, abandoned={len(pr_data.get('abandoned',[]) if pr_data else [])})")

if __name__ == "__main__":
    main()
