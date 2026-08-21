from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from enum import Enum
import os
import time
import shutil
import re
import json

def load_app_state(profile_dir):
    state_file = os.path.join(profile_dir, "app_state.json")
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            return json.load(f)
    return {}

def save_app_state(profile_dir, state):
    state_file = os.path.join(profile_dir, "app_state.json")
    with open(state_file, "w") as f:
        json.dump(state, f)
from selenium.common.exceptions import TimeoutException

# How long to wait for Play Console to land on the new app's dashboard.
CREATE_APP_TIMEOUT = 180

developer_id = None
app_id = None
driver = None
wait = None

class AppType(Enum):
    APP = "app"
    GAME = "game"

class AppPricing(Enum):
    FREE = "free"
    PAID = "paid"

def create_app(
    app_name: str,
    type: AppType = AppType.APP,
    default_language: str = "en-US",
    pricing: AppPricing = AppPricing.FREE
):
    # 4. Navigate to Create App page
    print("Navigating to Create App page...")
    driver.get(f"https://play.google.com/console/u/0/developers/{developer_id}/create-new-app")

    # 5. Fill app name
    print("Filling app name...")
    app_name_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='app-name-input'] input")))
    app_name_input.send_keys(app_name)

    # 6. Choose default language
    print("Choosing default language...")
    lang_dropdown = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "material-dropdown-select[debug-id='language-dropdown'] dropdown-button")))
    lang_dropdown.click()

    # Wait for dropdown options to appear and select English (US)
    time.sleep(1)  # Small delay for dropdown to open
    print(f"Selecting {default_language} language option...")
    lang_option = wait.until(EC.element_to_be_clickable((By.XPATH, f"//material-select-dropdown-item//span[contains(text(), '{default_language}')]")))
    lang_option.click()

    # Select between app or game
    print(f"Selecting '{type.value}' type...")
    app_type_radio = driver.find_element(By.CSS_SELECTOR, f"material-radio[debug-id='{type.value}-radio'] input[type='radio']")
    app_type_radio.click()

    # Select between free or paid
    print(f"Selecting {pricing.value} option...")
    pricing_radio = driver.find_element(By.CSS_SELECTOR, f"material-radio[debug-id='{pricing.value}-radio'] input[type='radio']")
    pricing_radio.click()

    # 7. Accept policy checkboxes
    print("Accepting policy checkboxes...")
    developers_police = driver.find_element(By.CSS_SELECTOR, "material-checkbox[debug-id='guidelines-checkbox']")
    # scroll down to make the checkbox clickable
    driver.execute_script("arguments[0].scrollIntoView(true);", developers_police)
    developers_police_input = developers_police.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
    developers_police_input.click()

    eua_exportation_policy = driver.find_element(By.CSS_SELECTOR, "material-checkbox[debug-id='export-laws-checkbox']")
    # scroll down to make the checkbox clickable
    driver.execute_script("arguments[0].scrollIntoView(true);", eua_exportation_policy)
    eua_exportation_policy_input = eua_exportation_policy.find_element(By.CSS_SELECTOR, "input[type='checkbox']")
    eua_exportation_policy_input.click()

    # 8. Submit form
    print("Submitting form to create app...")
    create_button = driver.find_element(By.CSS_SELECTOR, "material-button[debug-id='create-app-button'] button")
    create_button.click()

    print("Waiting for app creation to complete...")
    # Wait for https://play.google.com/console/u/0/developers/5537545705678883625/app/4972799964573781708/app-dashboard
    # Bounded: if the form comes back with a validation error the dashboard URL
    # never arrives, and an unbounded loop here hangs a CI job until its job
    # timeout instead of reporting the failure.
    dashboard_re = r'https://play\.google\.com/console/u/0/developers/[^/]+/app/\d+/app-dashboard'
    deadline = time.time() + CREATE_APP_TIMEOUT
    current_url = None
    while time.time() < deadline:
        try:
            wait.until(EC.url_matches(dashboard_re))
            current_url = driver.current_url
            if re.match(dashboard_re, current_url):
                print("App created successfully!")
                break
        except TimeoutException:
            time.sleep(2)
    else:
        raise RuntimeError(
            f"Play Console did not reach the app dashboard within {CREATE_APP_TIMEOUT}s. "
            f"Last URL: {driver.current_url}. The create-app form usually rejected "
            f"something — an app name already in use is the common cause."
        )

    app_id = re.search(r'/app/(\d+)/', current_url).group(1)
    return app_id

def click_save_button():
    save_button = driver.find_element(By.CSS_SELECTOR, "button[debug-id='main-button']")
    save_button.click()

def wait_for_success_icon():
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-icon[aria-hidden='true'] i.material-icons-extended[role='img']")))
        # Confirm it's the check_circle_outline icon
        icon = driver.find_element(By.CSS_SELECTOR, "material-icon[aria-hidden='true'] i.material-icons-extended[role='img']")
        if icon.text.strip() == "check_circle_outline":
            print("check_circle_outline icon appeared, privacy policy URL saved.")
        else:
            print("Icon appeared but is not check_circle_outline.")
    except TimeoutException:
        print("Timeout waiting for check_circle_outline icon after saving privacy policy URL.")

def fill_privacy_policy_form(privacy_policy_url):
    print("Navigating to Privacy Policy page...")
    driver.get( f"https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/app-content/privacy-policy")
    print("Fill Privacy policy URL...")
    app_name_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='privacy-policy-url-input'] input")))
    app_name_input.send_keys(privacy_policy_url)
    print("Submitting form to create app...")
    click_save_button()
    wait_for_success_icon()
    return True

class TestingCredential:
    def __init__(self, name, username, password, instructions):
        self.name = name
        self.username = username
        self.password = password
        self.instructions = instructions

def fill_testing_credentials_form(
    credentials: list[TestingCredential] = []
):
    # Navigate to Testing credentials page
    print("Navigating to Testing credentials page...")
    driver.get( f"https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/app-content/testing-credentials")

    # Fill in test credentials
    if len(credentials) == 0:
        print("Selecting radio inside login-not-required-expandable-section...")
        login_not_required_radio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "console-form-expandable-section[debug-id='login-not-required-expandable-section']")))
        login_not_required_radio.click()
        click_save_button()
        wait_for_success_icon()
        return True

    print("Selecting radio inside login-required-expandable-section...")
    login_required_radio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "console-form-expandable-section[debug-id='login-required-expandable-section'] input")))
    # scroll down to make the radio clickable
    driver.execute_script("arguments[0].scrollIntoView(true);", login_required_radio)
    login_required_radio.click()
    for credential in credentials:
        add_instructions_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[debug-id='add-credential-button']")))
        driver.execute_script("arguments[0].scrollIntoView(true);", add_instructions_button)
        add_instructions_button.click()
        name_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='name-input'] input")))
        name_input.send_keys(credential.name)
        username_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='username-input'] input")))
        username_input.send_keys(credential.username)
        password_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='password-input'] input")))
        password_input.send_keys(credential.password)
        instructions_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='additional-details-input'] textarea")))
        instructions_input.send_keys(credential.instructions)
        no_additional_info_required_checkbox = driver.find_element(By.CSS_SELECTOR, "material-checkbox[debug-id='no-additional-details-required-checkbox']")
        no_additional_info_required_checkbox.click()
        apply_button = driver.find_element(By.CSS_SELECTOR, "button[debug-id='apply-button']")
        apply_button.click()
    click_save_button()
    wait_for_success_icon()
    time.sleep(2)
    return True

def fill_ads_declaration_form(
    has_ads: bool = False
):
    # Navigate to Ads declaration page
    print("Navigating to Ads declaration page...")
    driver.get( f"https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/app-content/ads-declaration")

    # Select "Yes" or "No" for ads
    print(f"Selecting '{'Yes' if has_ads else 'No'}' for ads...")
    if has_ads:
        # Click first material-radio for "Yes"
        ads_radio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-radio:first-of-type input[type='radio']")))
    else:
        # Click second material-radio for "No"
        ads_radio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-radio:nth-of-type(2) input[type='radio']")))
    driver.execute_script("arguments[0].scrollIntoView(true);", ads_radio)
    ads_radio.click()

    click_save_button()
    wait_for_success_icon()
    return True

class AppCategory(Enum):
    GAME = 0
    SOCIAL = 1
    OTHER = 2
def fill_content_rating_overview(
    email: str,
    category: AppCategory = AppCategory.OTHER,
):
    # Navigate to Content Rating Overview page
    print("Navigating to Content Rating Overview page...")
    driver.get( f"https://play.google.com/console/u/0/developers/{developer_id}/app/{app_id}/app-content/content-rating-overview")

    # Wait for page to load
    time.sleep(2)

    try:
        # Look for the incomplete inputnaire section with "Editar" (Edit) button
        edit_buttons = driver.find_elements(By.CSS_SELECTOR, "console-section[debug-id='incomplete-inputnaire-section'] button[debug-id='edit-button']")
        if edit_buttons:
            edit_button = edit_buttons[0]
            print("Found incomplete inputnaire section. Clicking 'Editar' button...")
            driver.execute_script("arguments[0].scrollIntoView(true);", edit_button)
            edit_button.click()
            time.sleep(2)
            print("Clicked edit button for incomplete inputnaire.")
        else:
            print("Incomplete inputnaire section not found or already completed.")

    except Exception as e:
        print(f"Error checking for incomplete inputnaire: {e}")

    # fill email
    email_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "material-input[debug-id='email-address-input'] input")))
    email_input.send_keys(credential.email)

    # Set app category

    # Currently no actions defined for this page
    return True

def main():
    # These are module globals the page-flow functions above read; without
    # declaring them here, assigning below would create locals instead and
    # leave create_app() looking at a None driver.
    global developer_id, app_id, driver, wait

    # Paths
    home_dir = os.path.expanduser("~")
    original_profile = os.path.join(home_dir, ".config", "google-chrome", "Default")
    script_profile = os.path.join(home_dir, ".config", "google-chrome", "gplay-automation-profile")

    # If the profile doesn't exist, copy from Default (first run only)
    if not os.path.exists(script_profile):
    	shutil.copytree(original_profile, script_profile, dirs_exist_ok=True)

    # Chrome options
    options = Options()
    options.add_argument(f"user-data-dir={script_profile}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-blink-features=AutomationControlled")

    # Start browser
    print("Starting Chrome browser...")
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 15)

    # 1. Go to Play Console login
    print("Navigating to Play Console login page...")
    driver.get("https://accounts.google.com/ServiceLogin?service=androiddeveloper&passive=true&continue=https%3A%2F%2Fplay.google.com%2Fconsole%2Fdeveloper%2F")

    # 2. Check if we have a saved developer ID
    developer_id_file = os.path.join(script_profile, "developer_id.txt")

    if os.path.exists(developer_id_file):
        with open(developer_id_file, 'r') as f:
            developer_id = f.read().strip()
    else:
        print("No saved developer ID found. Please complete login and select developer profile...")

    # Wait until user finish login and select developer profile
    print("Waiting for user to finish login and select developer profile...")
    while not developer_id:
        print(f"Found saved Developer ID: {developer_id}")
        try:
            # Wait for URL to contain developer ID
            wait.until(EC.url_contains("https://play.google.com/console/u/0/developers/"))
            current_url = driver.current_url
            match = re.search(r'/developers/([^/]+)/', current_url)
            if match:
                developer_id = match.group(1)
                # Save the developer ID for future use
                with open(developer_id_file, 'w') as f:
                    f.write(developer_id)
                print(f"Developer ID found and saved: {developer_id}")
                break
            else:
                print("Please select your developer profile to continue...")
                time.sleep(2)
        except TimeoutException:
            time.sleep(1)
            continue


    # App state persistence
    app_name = "My Automated Test App"  # You may want to make this configurable
    app_state = load_app_state(script_profile)
    if app_name in app_state:
        app_id = app_state[app_name]["app_id"]
        print(f"Loaded app_id for '{app_name}': {app_id}")
    else:
        app_id = create_app(
            app_name=app_name,
            type=AppType.APP,
            default_language="en-US",
            pricing=AppPricing.FREE
        )
        app_state[app_name] = {"app_id": app_id}
        save_app_state(script_profile, app_state)
        print(f"Created new app_id for '{app_name}': {app_id}")

    # Start filling the forms
    print("Filling app forms...")
    if not app_state[app_name].get("privacy_policy_filled"):
        print("Filling privacy policy...")
        if fill_privacy_policy_form("https://www.example.com/privacy-policy"):
            app_state[app_name]["privacy_policy_filled"] = True
            save_app_state(script_profile, app_state)
    else:
        print("Privacy policy already filled for this app.")

    if not app_state[app_name].get("testing_credentials_filled"):
        print("Filling testing credentials...")
        credential = TestingCredential(
            name="Test Credential",
            username="testuser",
            password="Test@1234",
            instructions="Use the above credentials to log in."
        )
        credential2 = TestingCredential(
            name="Second Credential",
            username="seconduser",
            password="Second@1234",
            instructions="Use the above credentials to log in as second user."
        )
        if fill_testing_credentials_form([credential, credential2]):
            app_state[app_name]["testing_credentials_filled"] = True
            save_app_state(script_profile, app_state)
    else:
        print("Testing credentials already filled for this app.")

    if not app_state[app_name].get("ads_declaration_filled"):
        print("Filling ads declaration...")
        if fill_ads_declaration_form(has_ads=True):
            app_state[app_name]["ads_declaration_filled"] = True
            save_app_state(script_profile, app_state)

    print("Quitting browser...")
    driver.quit()


if __name__ == "__main__":
    main()
