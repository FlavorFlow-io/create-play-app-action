# Create Play App action

Creates a Google Play Console app for one FlavorFlow client, on a self-hosted
runner with a signed-in Chrome profile.

The Play Developer API cannot create an app — that is why this drives the
Console UI rather than calling an API.

```yaml
jobs:
  create-play-app:
    runs-on: [self-hosted, macOS]
    steps:
      - uses: FlavorFlow-io/create-play-app-action@v1
        with:
          api-key: ${{ secrets.FLAVORFLOW_API_KEY }}
          project-id: ${{ vars.FLAVORFLOW_PROJECT_ID }}
          client-id: ${{ inputs.client_id }}
```

## Two credentials, not interchangeable

| | Authenticates to | Supplied by |
|---|---|---|
| FlavorFlow API key | FlavorFlow | `api-key` input |
| Google session | Play Console | Chrome profile on the runner |

Nothing FlavorFlow issues can log in to Google. The API key says *what* to
create — app name and package name from the client record. The Google session
lives in a Chrome profile a human signed in once on the runner.

Automating the Google password login is not supported: it violates Google's
terms and in practice trips bot detection and a 2FA challenge that cannot be
answered from a script.

## When a human is needed

Almost never.

- **Session valid** — runs unattended, nobody notified.
- **Session expired** — the run pauses, raises a desktop notification on the
  runner (and posts to `notify-webhook` if set), leaves the Chrome window open,
  and waits up to `login-timeout` for a sign-in. Reminders repeat every
  `notify-interval`. Signing in resumes the run.
- **Nobody signs in** — fails with the last URL, so the retry starts from a
  known state rather than a hung job.

A slow console does not raise an alert; only an actual redirect to a login page
pages someone immediately.

## Runner requirements

A self-hosted machine with a display and Google Chrome. The action installs its
own Python dependencies into a venv, so nothing else is needed. One-time
sign-in, using a dedicated profile — never the runner user's everyday Chrome
profile, whose cookies for every other site would otherwise be reachable by any
workflow:

```bash
PROFILE=~/.config/google-chrome/gplay-automation-profile

# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$PROFILE" --profile-directory=Default \
  https://play.google.com/console/developers

# Linux
google-chrome --user-data-dir="$PROFILE" --profile-directory=Default \
  https://play.google.com/console/developers
```

Sign in, pick the developer account, confirm the URL becomes
`.../developers/<id>/...`, then close Chrome fully so the profile is not locked.

## Where the code comes from

`action.yml` and `ci_create_app.py` live here. The Play Console page flows in
`automation/` are published from the private `google-play-automation` repo by its
release process, so this repo can stay public and consumers need no token.

## Behaviour

- **Idempotent** — created apps are recorded in `app_state.json` inside the
  profile directory. A re-run reports the existing id with `created=false`.
- **Bounded** — every wait has a deadline. An expired session, a slow page and a
  rejected create form are three different messages.
