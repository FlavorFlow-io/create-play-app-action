"""
Creates a Play Console app for one FlavorFlow client, non-interactively.

Runs unattended while the runner's Chrome profile holds a valid Google session.
When that session is missing or expired — the only time a human is needed — it
notifies and waits for someone to sign in on the runner's own screen, then
carries on. It never waits silently and never waits forever.

Two different credentials are in play and they are not interchangeable:

  FlavorFlow API key -> reads the client (app name, package name) from FlavorFlow
  Chrome profile     -> carries the Google session that Play Console requires

No FlavorFlow credential can produce a Google session. The profile is
established once, by a human, on the runner itself; see ci/README.md.
"""
import argparse
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONSOLE_HOME = "https://play.google.com/console/developers"
# Grace period before alerting when the console was merely slow rather than
# explicitly asking for a login.
GRACE_BEFORE_ALERT = 60
DEVELOPER_URL_RE = re.compile(r"/developers/(\d+)")


def log(msg):
    print(msg, flush=True)


def fail(msg, hint=None):
    """Fails the step with a GitHub error annotation rather than a stack trace."""
    print(f"::error::{msg}", flush=True)
    if hint:
        print(hint, file=sys.stderr, flush=True)
    sys.exit(1)


def emit_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            fh.write(f"{name}={value}\n")


def summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write("\n".join(lines) + "\n")


# ── FlavorFlow ──────────────────────────────────────────────────────────────

def fetch_client(api_base, api_key, project_id, client_id):
    """The client as FlavorFlow knows it — the source of truth for the forms."""
    url = f"{api_base.rstrip('/')}/v1/projects/{project_id}/clients"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = json.load(res)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        fail(
            f"FlavorFlow returned {err.code} for {url}",
            "Check the api-key is a project API key for this project, not a workspace key.\n"
            f"Response: {detail}",
        )
    except Exception as err:  # network, DNS, timeout
        fail(f"Could not reach FlavorFlow at {url}: {err}")

    clients = body.get("data") or []
    if not clients:
        fail(f"Project {project_id} has no enabled clients.")

    match = next((c for c in clients if c.get("id") == client_id), None)
    if not match:
        known = ", ".join(f"{c.get('id')} ({c.get('name')})" for c in clients[:10])
        fail(
            f"No client {client_id!r} in project {project_id}.",
            f"Clients visible with this key: {known}",
        )
    return match


# ── Chrome / session ────────────────────────────────────────────────────────

def check_profile_lock(profile_dir):
    """
    A live lock means another Chrome already has this profile; Selenium's own
    error for that is inscrutable, so say it plainly. A missing profile is not
    an error — it just means nobody has signed in here yet, which the login
    wait below handles.
    """
    profile = Path(profile_dir)
    for lock in ("SingletonLock", "SingletonCookie"):
        if (profile / lock).exists():
            fail(
                f"Chrome profile {profile} is locked ({lock} present).",
                "Another Chrome is using this profile, or a previous run crashed and\n"
                "left the lock behind. Close Chrome on the runner (or delete the lock\n"
                f"file at {profile / lock} if nothing is running), then re-run.",
            )
    profile.mkdir(parents=True, exist_ok=True)


def notify(title, message, webhook_url="", desktop=True):
    """
    Best-effort shout that a human is needed. The runner has a screen and a
    person near it, so a desktop notification is the one most likely to be seen;
    the annotation covers whoever is watching the Actions tab, and the webhook
    covers everyone else. None of them are allowed to break the run.
    """
    print(f"::warning::{title} — {message}", flush=True)

    if desktop:
        for cmd in (
            ["notify-send", "--urgency=critical", title, message],
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
        ):
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=10)
                break
            except (FileNotFoundError, subprocess.SubprocessError):
                continue

    if webhook_url:
        try:
            payload = json.dumps({"text": f"*{title}*\n{message}"}).encode()
            req = urllib.request.Request(
                webhook_url, data=payload, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as err:
            print(f"::warning::Could not reach notify-webhook: {err}", flush=True)


def start_driver(profile_dir, chrome_binary, headless):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument(f"user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    # Play Console is an Angular app that behaves badly in tiny viewports.
    options.add_argument("--window-size=1600,1200")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if chrome_binary:
        options.binary_location = chrome_binary
    if headless:
        # Off by default: Google is markedly more likely to challenge a headless
        # session, and a challenge here is unrecoverable without a human.
        options.add_argument("--headless=new")
    return webdriver.Chrome(options=options)


def _developer_id_from(url):
    match = DEVELOPER_URL_RE.search(url or "")
    return match.group(1) if match else None


def _needs_login(url):
    return "accounts.google.com" in (url or "") or "/signin" in (url or "")


def resolve_developer_id(driver, opts):
    """
    Returns the Play Console developer id, waiting for a human only if the
    stored session cannot supply one.

    Phase 1 is a short poll: an authenticated profile lands on the console in
    seconds, so a healthy run never notifies anyone. Phase 2 starts only when
    Google asks for a sign-in, and is the one case where someone has to walk to
    the runner. It re-notifies periodically, because a single notification sent
    while nobody was looking is the same as none.
    """
    driver.get(CONSOLE_HOME)

    saw_login_page = False
    deadline = time.time() + opts.session_timeout
    while time.time() < deadline:
        url = driver.current_url
        if _needs_login(url):
            saw_login_page = True
            break
        developer_id = _developer_id_from(url)
        if developer_id:
            return developer_id
        time.sleep(2)

    url = driver.current_url
    developer_id = _developer_id_from(url)
    if developer_id:
        return developer_id

    # From here on a person is required.
    if opts.headless:
        fail(
            "The Google session has expired and Chrome is headless, so nobody can sign in.",
            "Re-run with headless disabled (the default), or sign in on the runner first.",
        )

    try:
        # Put the window where a passer-by will actually notice it.
        driver.maximize_window()
        driver.switch_to.window(driver.current_window_handle)
    except Exception:
        pass

    title = "FlavorFlow: Play Console sign-in needed"
    where = f"{os.environ.get('RUNNER_NAME') or platform.node()}"
    message = (
        f"Sign in to Play Console in the open Chrome window on {where}. "
        f"Waiting up to {opts.login_timeout // 60} min."
    )
    # Only page a human immediately when Google actually redirected to a login.
    # Reaching the timeout without seeing one is more often a slow console than
    # a dead session, and a false alarm teaches people to ignore real ones.
    if saw_login_page:
        notify(title, message, opts.notify_webhook, opts.notify_desktop)
        next_reminder = time.time() + opts.notify_interval
    else:
        log("Console did not resolve in time and no login page appeared — "
            "giving it a grace period before alerting anyone.")
        next_reminder = time.time() + min(opts.notify_interval, GRACE_BEFORE_ALERT)
    log(f"Waiting for sign-in on {where} (up to {opts.login_timeout}s)…")

    alerted = saw_login_page
    deadline = time.time() + opts.login_timeout
    while time.time() < deadline:
        developer_id = _developer_id_from(driver.current_url)
        if developer_id:
            remaining = int(deadline - time.time())
            log(f"Signed in — developer id {developer_id} (with {remaining}s to spare).")
            if alerted:
                notify(
                    "FlavorFlow: Play Console sign-in received",
                    "Continuing with app creation.",
                    opts.notify_webhook,
                    opts.notify_desktop,
                )
            return developer_id
        if time.time() >= next_reminder:
            left = int(deadline - time.time())
            notify(title, f"{message} ({left // 60} min left)",
                   opts.notify_webhook, opts.notify_desktop)
            alerted = True
            next_reminder = time.time() + opts.notify_interval
        time.sleep(3)

    fail(
        f"Nobody signed in within {opts.login_timeout}s.",
        f"Last URL was {driver.current_url}. Sign in on {where} and re-run — the\n"
        "session persists in the Chrome profile, so this should be a one-off.",
    )


# ── app state ───────────────────────────────────────────────────────────────

def load_state(profile_dir):
    path = Path(profile_dir) / "app_state.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            log(f"::warning::Ignoring corrupt {path}")
    return {}


def save_state(profile_dir, state):
    (Path(profile_dir) / "app_state.json").write_text(json.dumps(state, indent=2))


def load_page_flows(repo_root):
    """
    Imports the interactive tool for its page-flow functions. Its filename isn't
    a valid module name, hence the explicit spec.
    """
    path = Path(repo_root) / "gplay-createapp.py"
    if not path.exists():
        fail(f"Could not find {path} — is this running inside the addon checkout?")
    spec = importlib.util.spec_from_file_location("gplay_createapp", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="https://api.flavorflow.io")
    ap.add_argument("--api-key", default=os.environ.get("FLAVORFLOW_API_KEY", ""))
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--app-name", default="")
    ap.add_argument("--profile-dir", required=True)
    ap.add_argument("--chrome-binary", default="")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--default-language", default="en-US")
    ap.add_argument("--app-type", default="app", choices=["app", "game"])
    ap.add_argument("--pricing", default="free", choices=["free", "paid"])
    ap.add_argument("--session-timeout", type=int, default=60,
                    help="How long a valid stored session may take to land on the console")
    ap.add_argument("--login-timeout", type=int, default=1800,
                    help="How long to wait for a human to sign in when the session is gone")
    ap.add_argument("--notify-interval", type=int, default=300,
                    help="Seconds between reminders while waiting for sign-in")
    ap.add_argument("--notify-webhook", default=os.environ.get("FLAVORFLOW_NOTIFY_WEBHOOK", ""))
    ap.add_argument("--no-desktop-notify", dest="notify_desktop",
                    action="store_false", default=True)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.api_key:
        fail("No FlavorFlow API key. Pass api-key, or set FLAVORFLOW_API_KEY.")

    client = fetch_client(args.api_base, args.api_key, args.project_id, args.client_id)
    app_name = args.app_name or client.get("app_name") or client.get("name")
    package_name = client.get("package_name")
    if not app_name:
        fail(f"Client {args.client_id} has neither an app name nor a name.")

    log(f"Client       {client.get('name')} ({args.client_id})")
    log(f"App name     {app_name}")
    log(f"Package      {package_name or '(none set in FlavorFlow)'}")

    state = load_state(args.profile_dir) if Path(args.profile_dir).is_dir() else {}
    if app_name in state and state[app_name].get("app_id"):
        existing = state[app_name]["app_id"]
        log(f"Already created earlier as app_id={existing}; nothing to do.")
        emit_output("app-id", existing)
        emit_output("created", "false")
        summary([f"### Play app", f"- `{app_name}` already existed (`{existing}`)"])
        return 0

    if args.dry_run:
        log("--dry-run: would create the app; no browser started.")
        emit_output("app-id", "")
        emit_output("created", "false")
        return 0

    check_profile_lock(args.profile_dir)
    flows = load_page_flows(args.repo_root)

    driver = start_driver(args.profile_dir, args.chrome_binary, args.headless)
    try:
        from selenium.webdriver.support.ui import WebDriverWait

        developer_id = resolve_developer_id(driver, args)
        log(f"Developer id {developer_id}")

        # The page-flow functions read these as module globals.
        flows.driver = driver
        flows.wait = WebDriverWait(driver, 20)
        flows.developer_id = developer_id

        app_id = flows.create_app(
            app_name=app_name,
            type=flows.AppType(args.app_type),
            default_language=args.default_language,
            pricing=flows.AppPricing(args.pricing),
        )
        app_id = app_id or getattr(flows, "app_id", None)
        if not app_id:
            fail("create_app() finished without yielding an app id.")

        state[app_name] = {"app_id": app_id, "package_name": package_name}
        save_state(args.profile_dir, state)

        log(f"Created app_id={app_id}")
        emit_output("app-id", str(app_id))
        emit_output("created", "true")
        emit_output("developer-id", developer_id)
        summary(
            [
                "### Play app created",
                f"- Client: `{client.get('name')}`",
                f"- App: `{app_name}`",
                f"- Package: `{package_name or '—'}`",
                f"- App id: `{app_id}`",
            ]
        )
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
