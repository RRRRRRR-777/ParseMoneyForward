import datetime
import hashlib
import json
import os
import pickle
import re
import time
import traceback
from pprint import pprint

import jpholiday
import pyotp
import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from logrelay.line_relay import LineRelay
from random_user_agent.params import OperatingSystem, SoftwareName
from random_user_agent.user_agent import UserAgent
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv(verbose=True)

COOKIE_FILE = "cookies.pkl"
SCREENSHOT_FILE = "reload_screenshot.png"
DEBUG_OUTPUT_DIR = os.environ.get(
    "DEBUG_OUTPUT_DIR", os.path.join("tmp", "debug")
)
CHROMEDRIVER_PATH = os.environ.get(
    "CHROMEDRIVER_PATH", "/snap/bin/chromium.chromedriver"
)
global driver
driver = None

# LogRelayの初期化
line_relay = LineRelay(
    os.getenv("LINE_ACCESS_LOG_RELAY_TOKEN"),
    os.getenv("USER_ID"),
)

DEFAULT_LOGIN_URL = "https://moneyforward.com/users/sign_in"


def build_chrome_options():
    """Chromeのオプションを構築する（シンプル版）"""
    chrome_options = Options()

    # ユーザーデータの保存先を一意にする
    unique_dir = f"/tmp/chrome_user_data_{os.getpid()}"
    chrome_options.add_argument(f"--user-data-dir={unique_dir}")

    # ヘッドレスモードで起動する
    chrome_options.add_argument("--headless=new")

    # ユーザーエージェントの指定
    software_names = [SoftwareName.CHROME.value]
    operating_systems = [OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value]
    user_agent_rotator = UserAgent(
        software_names=software_names,
        operating_systems=operating_systems,
        limit=100,
    )
    chrome_options.add_argument(
        f"--user-agent={user_agent_rotator.get_random_user_agent()}"
    )

    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    # ウィンドウの初期サイズを最大化
    chrome_options.add_argument("--start-maximized")

    return chrome_options


def create_webdriver():
    """chromedriverのインスタンスを生成する"""
    options = build_chrome_options()
    service = Service(executable_path=CHROMEDRIVER_PATH)
    return webdriver.Chrome(service=service, options=options)


def attempt_cookie_login():
    """保存済みクッキーによるログインを試みる"""
    if driver is None:
        raise RuntimeError("WebDriverが初期化されていません")

    try:
        cookies = load_cookies(COOKIE_FILE)
    except FileNotFoundError:
        return False

    # クッキーをセットするために一度サイトを開く
    driver.get("https://moneyforward.com")
    add_cookies_to_driver(driver, cookies)

    # クッキーを適用するために再度ページにアクセス
    driver.get("https://moneyforward.com")
    time.sleep(5)  # ページ読み込みとJavaScript実行を待機

    print("✓ クッキーをロードしました")
    return True


def ensure_logged_in(email, password):
    """クッキー / 通常ログインのいずれかでログイン状態を確立する"""
    cookie_loaded = attempt_cookie_login()

    if cookie_loaded and is_logged_in():
        print("✓ クッキーでログイン成功")
        return

    if cookie_loaded:
        print("クッキーが無効です。ログインを実行します。")

    login_selenium(email, password)


def save_cookies(driver, file_path):
    """クッキーファイルの保存
    Args:
        driver: seleniumドライバー
        file_path: クッキーファイルのパス
    """
    with open(file_path, "wb") as file:
        pickle.dump(driver.get_cookies(), file)


def load_cookies(file_path):
    """クッキーファイルの読み込み
    Args:
        file_path = クッキーファイルのパス
    Returns:
        list: クッキーデータ
    """
    with open(file_path, "rb") as file:
        return pickle.load(file)


def add_cookies_to_driver(driver, cookies):
    """Selenium WebDriverにクッキーを追加
    Args:
        driver: seleniumドライバー
        cookies: クッキーデータ
    """
    driver.delete_all_cookies()  # 既存のクッキーをクリア
    for cookie in cookies:
        if "domain" in cookie:
            del cookie["domain"]
        driver.add_cookie(cookie)


def save_debug_screenshot(driver, filename):
    """デバッグ用スクリーンショットをtmp配下に保存"""
    try:
        os.makedirs(DEBUG_OUTPUT_DIR, exist_ok=True)
        path = os.path.join(DEBUG_OUTPUT_DIR, filename)
        driver.save_screenshot(path)
        print(f"デバッグ用スクリーンショット保存: {path}")
        return path
    except Exception as e:
        print(f"デバッグ用スクリーンショットの保存に失敗しました ({filename}): {e}")
        return None


def _xpath_literal(value):
    """XPathリテラルを生成"""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    concat_parts = []
    for idx, part in enumerate(parts):
        if part:
            concat_parts.append(f"'{part}'")
        if idx != len(parts) - 1:
            concat_parts.append("\"'\"")
    return "concat(" + ", ".join(concat_parts) + ")"


def _get_normalized_totp_secret():
    totp_secret = os.environ.get("TOTP_SECRET")
    if not totp_secret:
        return None
    normalized_secret = totp_secret.replace(" ", "").strip()
    return normalized_secret or None


def get_totp_code():
    """二段階認証コードを生成し、デバッグ情報も返す"""
    normalized_secret = _get_normalized_totp_secret()
    if not normalized_secret:
        raise ValueError("TOTP_SECRETが設定されていません")

    secret_length = len(normalized_secret)

    try:
        totp = pyotp.TOTP(normalized_secret)
        current_epoch = int(time.time())
        time_remaining = totp.interval - (current_epoch % totp.interval)
        if time_remaining < 5:
            wait_time = time_remaining + 1
            print(f"TOTPコード更新待ち ({wait_time}秒)...")
            time.sleep(wait_time)
            current_epoch = int(time.time())
            time_remaining = totp.interval - (current_epoch % totp.interval)

        code = totp.now()
        secret_checksum = hashlib.sha256(
            normalized_secret.encode("utf-8")
        ).hexdigest()[:12]
        debug_info = {
            "utc_time": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "timestamp": current_epoch,
            "time_remaining": time_remaining,
            "secret_length": secret_length,
            "secret_checksum": secret_checksum,
        }
        return code, debug_info
    except Exception as e:
        raise ValueError(f"TOTP_SECRETの形式が不正です: {e}")


def is_logged_in():
    """
    Seleniumを使用して、ユーザーがログインしているかを確認します。

    指定されたURL（https://moneyforward.com/accounts）にアクセスし、
    ログインページにリダイレクトされないかを確認します。

    Returns:
        bool: ログインしていればTrue、そうでなければFalseを返します。
    """
    url = "https://moneyforward.com/accounts"
    driver.get(url)
    time.sleep(3)  # ページ読み込みを待機

    current_url = driver.current_url
    print(f"ログイン確認 - アクセス先: {url}")
    print(f"ログイン確認 - 現在のURL: {current_url}")

    # sign_inやemail_otpにリダイレクトされたらログイン失敗
    if "/sign_in" in current_url or "/email_otp" in current_url:
        print("✗ ログイン失敗（ログインページにリダイレクトされました）")
        return False

    # /accountsまたはmoneyforward.comドメインにいればログイン成功
    if "/accounts" in current_url or (current_url.startswith("https://moneyforward.com") and "id.moneyforward.com" not in current_url):
        print("✓ ログイン成功")
        return True

    print("✗ ログイン失敗（予期しないURLです）")
    return False


def _wait_for_page_load(driver, timeout=60, max_attempts=3):
    """ページの読み込みとJavaScriptレンダリングを待機"""
    attempt_timeout = max(15, timeout // max_attempts)  # 最小15秒に延長
    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            time.sleep(3)
            email_element = WebDriverWait(driver, attempt_timeout).until(
                EC.visibility_of_element_located((By.XPATH, "//input[@type='email']"))
            )
            body_count = len(driver.find_elements(By.XPATH, "//body//*"))
            print(f"ページ読み込み完了 (要素数: {body_count})")
            return email_element
        except TimeoutException as e:
            last_exception = e
            screenshot_name = f"debug_login_page_retry{attempt-1}.png"
            save_debug_screenshot(driver, screenshot_name)

            current = driver.current_url or "about:blank"
            if current.startswith("chrome-error://") or current == "about:blank":
                print(f"警告: Chromeのエラーページまたは空ページが表示されています (URL: {current})")

            message = f"メール入力欄の検出に失敗しました ({attempt}/{max_attempts})。"
            if attempt == max_attempts:
                print(message + "試行回数の上限に達しました。")
                break

            print(message + "ログインページを再取得します...")
            driver.get(DEFAULT_LOGIN_URL)
            time.sleep(5)

    raise last_exception


def _dump_debug_page(driver, label):
    """デバッグ用に現在のHTMLを保存"""
    timestamp = int(time.time())
    path = f"/tmp/mf_debug_{label}_{timestamp}.html"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"デバッグ用HTMLを保存しました: {path}")
    except Exception as e:
        print(f"デバッグHTMLの保存に失敗しました: {e}")


def _handle_totp_authentication(driver, max_attempts=3):
    """TOTP二段階認証を処理"""
    print("TOTP認証開始")
    time.sleep(5)

    for attempt in range(1, max_attempts + 1):
        print(f"\n--- TOTP試行 {attempt}/{max_attempts} ---")

        # TOTP_SECRETからコードを生成
        totp_code, totp_debug = get_totp_code()
        print(f"生成されたコード: {totp_code} | 残り{totp_debug['time_remaining']}秒")

        try:
            # TOTP入力欄を探す
            totp_input = None
            try:
                totp_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[inputmode='numeric']"))
                )
                print("✓ TOTP入力欄を検出")
            except:
                try:
                    totp_input = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='tel']"))
                    )
                    print("✓ TOTP入力欄を検出 (tel type)")
                except:
                    pass

            if not totp_input:
                print("エラー: TOTP入力欄が見つかりません")
                if attempt == max_attempts:
                    raise Exception("TOTP入力欄が見つかりませんでした")
                time.sleep(5)
                continue

            # コードを入力
            print(f"コードを入力: {totp_code}")
            totp_input.clear()
            totp_input.send_keys(totp_code)
            time.sleep(1)

            # 送信ボタンを探してクリック
            submit_button = None
            try:
                submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                print("✓ 送信ボタンを検出")
            except:
                try:
                    submit_button = driver.find_element(By.XPATH, "//button")
                    print("✓ 送信ボタンを検出 (汎用)")
                except:
                    pass

            if not submit_button:
                print("エラー: 送信ボタンが見つかりません")
                if attempt == max_attempts:
                    raise Exception("送信ボタンが見つかりませんでした")
                time.sleep(5)
                continue

            # ボタンをクリック
            print("送信ボタンをクリック...")
            submit_button.click()
            time.sleep(2)

            # 認証完了を待つ
            print("認証結果を待機中...")
            try:
                WebDriverWait(driver, 30).until(
                    lambda d: not d.current_url.startswith("https://id.moneyforward.com/two_factor_auth")
                )
                print("✓ TOTP認証成功")
                return
            except TimeoutException:
                error_elements = driver.find_elements(
                    By.XPATH, "//p[contains(text(), 'コードが間違っています')]"
                )
                if error_elements and attempt < max_attempts:
                    print("✗ TOTPコードが拒否されました。次のコードで再試行します...")
                    time.sleep(5)
                    continue
                raise Exception("TOTP認証を完了できませんでした")

        except Exception as e:
            print(f"エラー: {e}")
            if attempt == max_attempts:
                raise
            time.sleep(5)


def _complete_login_and_save_cookies(driver):
    """ログイン完了確認とクッキー保存

    Args:
        driver: Seleniumドライバー

    Raises:
        Exception: ログイン確認失敗時
    """
    print(f"ログイン完了確認を開始します。現在のURL: {driver.current_url}")

    target_xpath = "//a[contains(@href, 'moneyforward.com')]"

    def _is_portal_ready(d):
        current = d.current_url or ""
        return (
            current.startswith("https://moneyforward.com")
            or len(d.find_elements(By.XPATH, target_xpath)) > 0
        )

    try:
        WebDriverWait(driver, 60).until(_is_portal_ready)
    except TimeoutException:
        print("ログイン後の遷移要素が見つかりませんでした。")
        _dump_debug_page(driver, "login_timeout")
        raise

    if not driver.current_url.startswith("https://moneyforward.com"):
        portal_links = driver.find_elements(By.XPATH, target_xpath)
        if not portal_links:
            raise Exception("マネーフォワード本体へのリンクが検出できません")

        target_link = portal_links[0]
        for link in portal_links:
            href = (link.get_attribute("href") or "").strip()
            print(f"検出したリンク: {href}")
            if "auth" in href or "callback" in href:
                target_link = link
                break

        print("マネーフォワード本体へのリンクをクリックします...")
        driver.execute_script("arguments[0].click();", target_link)
        WebDriverWait(driver, 60).until(
            lambda d: (d.current_url or "").startswith(
                "https://moneyforward.com"))

    # account_selectorページを処理
    if "/account_selector" in driver.current_url:
        print("アカウント選択ページを検出しました。最初のアカウントを選択します...")
        try:
            # アカウント選択ボタンを探す（複数ある場合は最初のものを選択）
            account_buttons = driver.find_elements(By.XPATH, "//a[contains(@href, 'moneyforward.com')]")
            if account_buttons:
                print(f"{len(account_buttons)}個のアカウントが見つかりました。最初のアカウントを選択します...")
                driver.execute_script("arguments[0].click();", account_buttons[0])
                time.sleep(3)
                # アカウント選択後、マネーフォワード本体への遷移を待つ
                print("アカウント選択後の遷移を待機中...")
                WebDriverWait(driver, 30).until(
                    lambda d: "moneyforward.com" in d.current_url and "/account_selector" not in d.current_url
                )
                print(f"✓ アカウント選択後のURL: {driver.current_url}")
            else:
                print("警告: アカウント選択ボタンが見つかりませんでした")
        except Exception as e:
            print(f"アカウント選択中にエラーが発生しました: {e}")

    # まだaccount_selectorにいる、またはログインページにいる場合
    if "/accounts" not in driver.current_url and "ptn=" not in driver.current_url:
        print("マネーフォワード本体へ遷移します...")
        driver.get("https://moneyforward.com")
        time.sleep(5)

    # 最終確認: account_selectorに戻されていないかチェック
    if "/account_selector" in driver.current_url:
        print("エラー: account_selectorから抜け出せませんでした")
        raise Exception("アカウント選択に失敗しました")

    print(f"✓ ログイン完了 現在のURL: {driver.current_url}")
    time.sleep(3)  # セッション確立を待つ

    # クッキーを保存
    save_cookies(driver, COOKIE_FILE)
    print(f"✓ クッキーの保存が完了しました")
    print(f"  保存先: {COOKIE_FILE}")
    print(f"  現在のURL: {driver.current_url}")


def login_selenium(email, password):
    """Seleniumライブラリでログインする

    Args:
        email str: moneyforwordのメールアドレス
        password str: moneyforwordのパスワード

    Raises:
        Exception: ログイン失敗時
    """
    global driver

    max_login_attempts = 3

    for attempt in range(1, max_login_attempts + 1):
        print(f"\n=== ログイン試行 {attempt}/{max_login_attempts} ===")
        print(f"ログインページにアクセスします... ({DEFAULT_LOGIN_URL})")
        driver.get(DEFAULT_LOGIN_URL)

        # ページ読み込みとメール入力欄の検出
        try:
            email_element = _wait_for_page_load(driver)
        except Exception as e:
            print(f"ページ読み込みエラー: {e}")
            if attempt == max_login_attempts:
                raise
            print("ページ読み込みに失敗したため再試行します...")
            continue

        try:
            # メールアドレス入力
            print("メールアドレスを入力します...")
            email_element.send_keys(email)
            time.sleep(1)

            # [ログインする]ボタン押下(パスワード入力前に必要)
            driver.find_element(by=By.XPATH, value="//*[@id='submitto']").click()
            time.sleep(1)

            # パスワード入力
            print("パスワードを入力します...")
            password_element = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//input[@type='password']"))
            )
            password_element.send_keys(password)

            # ログインボタン押下
            driver.find_element(by=By.XPATH, value="//*[@id='submitto']").click()
            time.sleep(5)

            print(f"認証後のURL: {driver.current_url}")

            # メール認証（email_otp）が要求されているか確認
            if "/email_otp" in driver.current_url:
                raise Exception(
                    "メール認証が要求されています。\n"
                    "マネーフォワードで二段階認証（TOTP）を設定してください。\n"
                    "設定後、環境変数TOTP_SECRETにシークレットキーを設定してください。"
                )

            # 二段階認証コード入力（TOTP）
            if "/two_factor_auth/totp" in driver.current_url or "/totp" in driver.current_url:
                _handle_totp_authentication(driver)
            else:
                print(f"二段階認証は不要です。現在のURL: {driver.current_url}")

            # ログイン完了とクッキー保存
            _complete_login_and_save_cookies(driver)
            return

        except Exception as e:
            print(f"ログインエラー: {e}")
            if attempt == max_login_attempts:
                raise
            print("再試行のためにログインフローをリセットします...")
            driver.delete_all_cookies()
            time.sleep(5)


def click_reloads_selenium():
    """
    Seleniumを使用して、マネーフォワードの「更新」ボタンを全てクリックします。

    XPATHで「更新」ボタンを取得し、順番にクリックします。エラーが発生した場合には、
    エラーメッセージを表示します。

    Raises:
        Exception: ボタンのクリック中に発生したエラーを表示します。
    """
    # トップページにアクセス
    toppage_url = "https://moneyforward.com"
    print(f"トップページにアクセスします: {toppage_url}")
    driver.get(toppage_url)

    # ページが完全に読み込まれるまで待機
    print("ページの読み込みを待機中...")
    time.sleep(5)

    # account_selectorに戻された場合の処理
    if "/account_selector" in driver.current_url:
        print("警告: account_selectorページにリダイレクトされました")
        try:
            account_buttons = driver.find_elements(By.XPATH, "//a[contains(@href, 'moneyforward.com')]")
            if account_buttons:
                print(f"アカウントを再選択します...")
                driver.execute_script("arguments[0].click();", account_buttons[0])
                time.sleep(5)
        except Exception as e:
            print(f"アカウント再選択エラー: {e}")

    print(f"現在のURL: {driver.current_url}")

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "registered-accounts"))
        )
        selectors = [
            "//a[contains(@href, '/aggregation_queue') and contains(normalize-space(.), '更新')]",
            "//button[contains(normalize-space(.), '更新')]",
            "//input[@value='更新' or @data-disable-with='更新']",
        ]

        def collect_button_infos():
            infos = []
            seen_keys = set()
            for selector in selectors:
                for element in driver.find_elements(By.XPATH, selector):
                    if not element.is_displayed() or not element.is_enabled():
                        continue
                    tag = element.tag_name.lower()
                    info = {
                        "tag": tag,
                        "href": element.get_attribute("href") or "",
                        "href_dom": element.get_dom_attribute("href") or "",
                        "value": element.get_attribute("value") or "",
                        "data": element.get_attribute("data-disable-with") or "",
                        "text": (element.text or "").strip(),
                    }
                    key_source = (
                        info["href_dom"]
                        or info["href"]
                        or info["value"]
                        or info["data"]
                        or info["text"]
                    )
                    if not key_source:
                        key_source = element.get_attribute("outerHTML")[:80]
                    key = f"{tag}:{key_source}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    info["key"] = key
                    infos.append(info)
            return infos

        def locate_button(info):
            tag = info["tag"]
            if tag == "a":
                xpath_candidates = []
                if info["href_dom"]:
                    xpath_candidates.append(f"//a[@href={_xpath_literal(info['href_dom'])}]")
                if info["href"]:
                    xpath_candidates.append(f"//a[@href={_xpath_literal(info['href'])}]")
                tail = (info["href_dom"] or info["href"]).split("/")[-1]
                if tail:
                    xpath_candidates.append(
                        f"//a[contains(@href, {_xpath_literal(tail)}) and contains(normalize-space(.), '更新')]"
                    )
                for xpath in xpath_candidates:
                    try:
                        return driver.find_element(By.XPATH, xpath)
                    except NoSuchElementException:
                        continue
                raise NoSuchElementException("更新リンクを再取得できませんでした")
            if tag == "input":
                if info["value"]:
                    return driver.find_element(By.XPATH, f"//input[@value={_xpath_literal(info['value'])}]")
                if info["data"]:
                    return driver.find_element(By.XPATH, f"//input[@data-disable-with={_xpath_literal(info['data'])}]")
            if tag == "button" and info["text"]:
                return driver.find_element(
                    By.XPATH,
                    f"//button[contains(normalize-space(.), {_xpath_literal(info['text'])})]",
                )
            raise NoSuchElementException("更新ボタンを再取得できませんでした")

        button_infos = collect_button_infos()
        print(f"{len(button_infos)}個の更新ボタンが見つかりました")
        for idx, info in enumerate(button_infos, start=1):
            try:
                button = locate_button(info)
            except NoSuchElementException as e:
                print(f"  - 更新ボタン {idx} を再取得できませんでした: {e}")
                continue

            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                    button,
                )
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", button)
                print(f"  - 更新ボタン {idx} をクリックしました (key: {info['key']})")
                time.sleep(2)
            except Exception as click_error:
                print(f"  - 更新ボタン {idx} のクリックに失敗しました: {click_error}")
        if button_infos:
            print("すべての更新ボタンに対するクリックを試行しました。処理待ちとして5秒待機します。")
            time.sleep(5)
    except Exception as e:
        print(f"更新ボタンのクリック中にエラーが発生しました。\n{e}")


def extract_number(text):
    """正規表現でマイナス記号と数字を抽出

    Args:
        text (str): 抽出元の文字列

    Returns:
        int: マッチした場合はその値をそうでない場合は0を格納する
    """
    match = re.search(r"-?\d+,?\d+", text)
    if match:
        # 抽出した値のカンマを除去して整数に変換
        return int(match.group().replace(",", ""))

    return 0


def get_all_amount():
    """すべての口座の値を取得

    Returns:
        str: 口座の値
    """
    # 現在のURLを確認し、トップページにいない場合のみアクセス
    toppage_url = "https://moneyforward.com"
    current_url = driver.current_url or ""

    if not current_url.startswith(toppage_url) or "/account_selector" in current_url:
        print(f"トップページに遷移します（現在: {current_url}）")
        driver.get(toppage_url)
        time.sleep(5)  # ページ読み込み待機を延長

        # account_selectorに戻された場合の処理
        if "/account_selector" in driver.current_url:
            print("account_selectorページが表示されました。アカウントを選択します...")
            try:
                account_buttons = driver.find_elements(By.XPATH, "//a[contains(@href, 'moneyforward.com')]")
                if account_buttons:
                    driver.execute_script("arguments[0].click();", account_buttons[0])
                    time.sleep(5)
            except Exception as e:
                print(f"アカウント選択エラー: {e}")

    print(f"口座情報の取得を開始します。現在のURL: {driver.current_url}")

    # ログイン前のページが表示されていないか確認
    try:
        before_login = driver.find_element(By.CLASS_NAME, "before-login-home-content")
        if before_login:
            print("警告: ログイン前のページが表示されています。ページをリフレッシュします...")
            driver.refresh()
            time.sleep(5)
    except:
        pass  # before-login-home-contentが見つからない = ログイン済み

    # registered-accounts要素が表示されるまで待機
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "registered-accounts"))
        )
        print("✓ registered-accounts要素が見つかりました")
    except Exception as e:
        print(f"Warning: 'registered-accounts' section not loaded within timeout: {e}")
        # デバッグ用スクリーンショットとHTML保存
        try:
            save_debug_screenshot(driver, "debug_get_all_amount.png")
            with open("debug_get_all_amount.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("デバッグ用HTMLを保存しました: debug_get_all_amount.html")
        except Exception as save_err:
            print(f"デバッグファイル保存エラー: {save_err}")

    # Beautiful Soupでパース
    soup = BeautifulSoup(driver.page_source, "html.parser")

    li_elements = []
    try:
        section = soup.find("section", id="registered-accounts")
        if section:
            li_elements = section.find_all(
                "li", class_=["heading-category-name", "account"]
            )
        else:
            print("Warning: 'registered-accounts' section not found.")
    except AttributeError as e:
        print(f"Error: {e}")
    if not li_elements:
        print("No 'li' elements found.")
    # 出力を格納する辞書
    all_amount = {}
    # 各liタグを処理
    for li in li_elements:
        if "heading-category-name" in li["class"]:
            heading = li.text.strip()
            if heading not in all_amount:
                all_amount[heading] = []
        elif "account" in li["class"]:
            # 口座名
            bank_name = li.find("a").text
            # 使用高
            amount_ = li.find("ul", class_="amount").find(
                "li", class_="number")
            amount = extract_number(amount_.text) if amount_ else 0
            # 残高
            balance_ = li.find("ul", class_="amount").find(
                "li", class_="balance")
            balance = extract_number(balance_.text) if balance_ else 0

            account_data = {
                "bank_name": bank_name,
                "number": amount,
                "balance": balance,
            }

            all_amount[heading].append(account_data)

    return all_amount


class CreateMonthlyBalancePage:
    def __init__(self, notion_token, parent_page_id):
        self.notion_token = notion_token
        self.parent_page_id = parent_page_id
        self.headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }

    def is_payday(self):
        """
        今日が給料日    あるかを確認します。

        給料日は通常毎月25日ですが、次の条件に従います:
        1. 25日が土曜日の場合は24日が給料日となる。
        2. 25日が土日祝日の場合は、25日以前で最も近い平日が給料日となる。

        Returns:
            bool: 今日が給料日ならTrue、そうでなければFalseを返します。
        """
        today = datetime.date.today()

        # 当月の25日を取得
        payday = datetime.date(today.year, today.month, 25)

        # 25日が土日または祝日であれば、直近の平日を取得
        while payday.weekday() >= 5 or jpholiday.is_holiday(payday):
            payday -= datetime.timedelta(days=1)

        # 今日が給料日かどうか確認
        return today == payday

    def get_database_id_from_json(self, json_file_path):
        """
        JSONファイルから page_id (または database_id) を取得する関数。

        Args:
            json_file_path (str): JSONファイルのパス。

        Returns:
            str: JSON内のpage_idの値。存在しない場合はNone。
        """
        try:
            with open(json_file_path, "r") as json_file:
                json_data = json.load(json_file)
            return json_data.get("page_id")
        except FileNotFoundError:
            print(f"警告: {json_file_path} が見つかりません。新しいデータベースを作成します。")
            return None

    def update_json_file(self, json_file_path, key, value):
        """
        JSONファイルを読み込み、指定したキーの値を更新する関数。

        Args:
            json_file_path (str): JSONファイルのパス。
            key (str): 更新するキー。
            value (str): 新しい値。

        """
        with open(json_file_path, "r") as json_file:
            json_data = json.load(json_file)

        json_data[key] = value

        with open(json_file_path, "w") as json_file:
            json.dump(json_data, json_file, indent=4)

    def get_value_from_dict(self, all_amount, key, bank_name, default=None):
        """
        指定された辞書から、特定の銀行やカードの値を取得する関数。

        Args:
            all_amount (dict): 銀行やカードの情報が含まれる辞書。
            key (str): 辞書のキー（"銀行"や"カード"など）。
            bank_name (str): 取得する銀行やカードの名前。
            default: 値が見つからない場合に返すデフォルト値。

        Returns:
            int: 取得した値。
        """
        return next(
            (item for item in all_amount[key]
             if item["bank_name"] == bank_name),
            default,
        )

    def get_database(self, database_id):
        """Notionデータベースの値を取得する

        Returns:
            list: Notionデータベースの値
        """
        notion_database = []
        # URLを関数内で定義
        url = f"https://api.notion.com/v1/databases/{database_id}/query"

        response = requests.post(url, headers=self.headers)
        if response.status_code != 200:
            print(
                f"データベースの取得中にエラーが発生しました。ステータスコード: {response.status_code}"
            )
        results = response.json().get("results", [])

        for result in results:
            name = result["properties"]["名前"]["title"][0].get(
                "plain_text", "N/A")
            price = result["properties"]["金額"].get("number", "N/A")
            notion_database.append({"name": name, "price": price})

        return notion_database

    def create_database(self):
        """
        Notion APIを使用して、新しいデータベースを作成します。

        Returns:
            str: 作成されたデータベースのID。エラーが発生した場合はNoneを返します。
        """
        # 現在の日付と月を取得
        current_month = datetime.datetime.now()
        # 1ヶ月加える
        month = (current_month + relativedelta(months=1)).month

        data = {
            "parent": {"type": "page_id", "page_id": self.parent_page_id},
            "title": [{"type": "text", "text": {"content": f"{month}月度のお金"}}],
            "icon": {"type": "emoji", "emoji": "💵"},
            "properties": {
                "名前": {"title": {}},
                "金額": {"number": {"format": "yen"}},
                "資産/負債": {
                    "multi_select": {
                        "options": [
                            {"name": "資産", "color": "blue"},
                            {"name": "負債", "color": "red"},
                            {"name": "貯金", "color": "yellow"},
                            {"name": "非表示", "color": "gray"},
                        ]
                    }
                },
                "備考": {"rich_text": {}},
            },
        }

        response = requests.post(
            "https://api.notion.com/v1/databases",
            headers=self.headers,
            data=json.dumps(data),
        )

        if response.status_code == 200:
            return response.json()["id"]
        else:
            print(
                f"データベース作成中にエラーが発生しました。ステータスコード: {response.status_code}"
            )
            print(response.text)
            return None

    def create_page(self, database_id, name, amount, categories, note, icon_emoji=None):
        """
        Notion APIを使用して、新しいページを作成します。

        Args:
            database_id (str): ページを作成するデータベースのID。
            name (str): ページの名前（タイトル）。
            amount (int): ページの金額。
            categories (list of str): 資産/負債のカテゴリ。
            note (str): ページの備考。
            icon_emoji (str, optional): ページのアイコンとして表示する絵文字。デフォルトはNone。

        Returns:
            str: 作成されたページのID。エラーが発生した場合はNoneを返します。
        """
        data = {
            "parent": {"database_id": database_id},
            "properties": {
                "名前": {"title": [{"text": {"content": name}}]},
                "金額": {"number": int(amount)},
                "資産/負債": {
                    "multi_select": [{"name": category} for category in categories]
                },
                "備考": {"rich_text": [{"text": {"content": note}}]},
            },
        }

        # アイコンを指定する場合
        if icon_emoji:
            data["icon"] = {"type": "emoji", "emoji": icon_emoji}

        # Notionの認証トークン
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=self.headers,
            data=json.dumps(data),
        )

        if response.status_code == 200:
            return response.json()["id"]
        else:
            print(
                f"ページ '{name}' の作成中にエラーが発生しました。ステータスコード: {response.status_code}"
            )
            print(response.text)
            return None

    def create_multiple_pages(self, database_id, pages_data):
        """
        Notion APIを使用して、指定されたデータに基づき複数のページを作成します。

        Args:
            database_id (str): ページを作成するデータベースのID。
            pages_data (list of dict): 各ページに関するデータのリスト。各辞書は、名前、金額、カテゴリ、備考、アイコンなどの情報を含みます。

        Returns:
            list of str: 作成されたページのIDのリスト。
        """
        created_pages = []
        for page_data in pages_data:
            page_id = self.create_page(database_id, **page_data)
            if page_id:
                created_pages.append(page_id)

        return created_pages

    def main(self, all_amount):
        """
        Notion APIを使用して、月次の資産負債を管理するページを作成し、金額の合計を計算して表示します。

        Args:
             all_amount (dict)： 様々な資産と負債の金額を含む辞書。
        """

        current_month_balance = 0
        json_file_path = "month-page-id.json"

        # # 暫定対応
        # database_id = self.get_database_id_from_json(json_file_path)
        # notion_database = self.get_database(database_id)
        # current_month_balance = sum(item["price"]
        #                             for item in notion_database)

        # return current_month_balance

        # 給料日ではない日の処理
        if not self.is_payday():
            # database_idを取得して現在の残高を計算
            database_id = self.get_database_id_from_json(json_file_path)

            if database_id is None:
                print("database_idが見つかりません。残高を0として返します。")
                return 0

            notion_database = self.get_database(database_id)
            current_month_balance = sum(item["price"]
                                        for item in notion_database)

            return current_month_balance
        # 給料日の処理
        else:
            # データベースを新規作成し、IDをJSONに書き込む
            database_id = self.create_database()
            self.update_json_file(json_file_path, "page_id", database_id)

            # 必要な値を取得
            bank_balance = self.get_value_from_dict(
                all_amount, "銀行", "三井住友銀行"
            ).get("number")
            card_data = self.get_value_from_dict(
                all_amount, "カード", "三井住友カード", {}
            )
            current_credit = card_data.get("number")
            next_credit = (
                card_data.get("balance", 0) -
                current_credit if current_credit else None
            )

            # 環境変数から値を取得
            env_vars = [
                "HOUSE_BANK",
                "RAKUTEN_BANK",
                "HOUSE_RENT",
                "FIXED_COST",
                "FOOD_EXPENSE",
            ]
            house_bank, rakuten_bank, house_rent, fixed_cost, food_expense = map(
                int, [os.environ[var] for var in env_vars]
            )

            if database_id:
                # 複数のページを作成
                pages_to_create = [
                    {
                        "icon_emoji": "🍳",
                        "name": "お自炊",
                        "amount": food_expense,
                        "categories": ["負債"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "🚰",
                        "name": "固定費",
                        "amount": fixed_cost,
                        "categories": ["負債"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "🏠",
                        "name": "家賃",
                        "amount": house_rent,
                        "categories": ["負債"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "💳",
                        "name": "来月の支払い",
                        "amount": next_credit,
                        "categories": ["負債"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "💸",
                        "name": "今月の支払い",
                        "amount": current_credit,
                        "categories": ["負債"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "🎇",
                        "name": "楽天銀行",
                        "amount": rakuten_bank,
                        "categories": ["資産"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "🧰",
                        "name": "お家銀行",
                        "amount": house_bank,
                        "categories": ["資産"],
                        "note": "",
                    },
                    {
                        "icon_emoji": "🏦",
                        "name": "銀行預金",
                        "amount": bank_balance,
                        "categories": ["資産"],
                        "note": "",
                    },
                ]

                # 複数のページを作成
                self.create_multiple_pages(database_id, pages_to_create)

                # 金額の残りを計算
                sum_list = [
                    bank_balance,
                    current_credit,
                    next_credit,
                    house_bank,
                    rakuten_bank,
                    house_rent,
                    fixed_cost,
                    food_expense,
                ]
                current_month_balance = sum(sum_list)

                return current_month_balance


def get_current_month_expense():
    """
    現在の月の支出額を取得します。

    SeleniumとBeautifulSoupを使って、マネーフォワードの支出概要ページから
    現在の月の支出合計を取得します。

    Returns:
        int: 現在の月の支出合計を数値として返します。
    """
    summary_url = "https://moneyforward.com/cf/summary"
    print(f"支出サマリページにアクセスします: {summary_url}")
    driver.get(summary_url)
    time.sleep(5)  # ページ読み込み待機

    # account_selectorに戻された場合の処理
    if "/account_selector" in driver.current_url:
        print("account_selectorページが表示されました。アカウントを選択します...")
        try:
            account_buttons = driver.find_elements(By.XPATH, "//a[contains(@href, 'moneyforward.com')]")
            if account_buttons:
                driver.execute_script("arguments[0].click();", account_buttons[0])
                time.sleep(5)
                # 再度サマリページにアクセス
                driver.get(summary_url)
                time.sleep(5)
        except Exception as e:
            print(f"アカウント選択エラー: {e}")

    print(f"現在のURL: {driver.current_url}")

    # monthly-total要素が表示されるまで待機
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "monthly-total"))
        )
        print("✓ monthly-total要素が見つかりました")
    except Exception as e:
        print(f"Warning: 'monthly-total' section not loaded within timeout: {e}")
        # デバッグ用スクリーンショット
        try:
            save_debug_screenshot(driver, "debug_get_current_month_expense.png")
        except:
            pass

    soup = BeautifulSoup(driver.page_source, "html.parser")
    monthly_total_section = soup.find("section", id="monthly-total")

    if not monthly_total_section:
        raise Exception("'monthly-total' section not found in page")

    tbody = monthly_total_section.find("tbody")
    if not tbody:
        raise Exception("'tbody' not found in monthly-total section")

    td_elements = tbody.find_all("td")
    if not td_elements:
        raise Exception("No 'td' elements found in tbody")

    current_month_expense_ = td_elements[-1]
    current_month_expense = extract_number(
        current_month_expense_.text.replace("\n", "")
    )

    return current_month_expense


def calculate_balance(all_amount, current_month_balance, current_month_expense):
    """
    月初の残高と証券口座の情報を基に、バランスシートを計算します。

    資産情報（all_amount）、現在の残高、および現在の支出を基にして、
    合計の残高と証券口座の情報を出力します。

    Args:
        all_amount (dict): 資産や負債に関するデータ。
        current_month_balance (int): 現在の残高。
        current_month_expense (int): 現在の月の支出額。

    Returns:
        tuple: 計算された残高と証券口座の情報を文字列として返します。
    """
    stock_list = []

    # マネーフォワードの証券口座
    for category, items in all_amount.items():
        for item in items:
            if category == "証券":
                stock_list.append(
                    {"name": item["bank_name"], "price": item["number"]})

    # 月初の残高 - 現在の支出
    balance_ = current_month_balance + current_month_expense
    balance = f"{balance_:,}円"
    stock = "\n".join(
        [f"{item['name']}: {item['price']:,}円" for item in stock_list])

    return balance, stock


def send_line_message(context):
    """LineNotifyでメッセージを送信する

    Args:
        context str: 送信する文字列
    """
    # APIのURLとトークン
    LINE_API_URL = "https://api.line.me/v2/bot/message/push"
    load_dotenv(verbose=True)
    LINE_ACCESS_PARSE_MONEY_FORWORD_TOKEN = os.environ["LINE_ACCESS_PARSE_MONEY_FORWORD_TOKEN"]
    USER_ID = os.environ["USER_ID"]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_PARSE_MONEY_FORWORD_TOKEN}",
    }
    data = {
        "to": USER_ID,
        "messages": [{"type": "text", "text": context}],
    }

    # メッセージを送信
    try:
        response = requests.post(
            LINE_API_URL, headers=headers, json=data)
        response.raise_for_status()  # HTTPエラーがある場合は例外を発生
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}


def main():
    load_dotenv(verbose=True)

    # 環境変数の値を読み込む
    EMAIL = os.environ["EMAIL"]
    PASSWORD = os.environ["PASSWORD"]
    NOTION_TOKEN = os.environ["NOTION_KEY"]
    PARENT_PAGE_ID = os.environ["NOTION_PAGE_ID"]

    global driver
    driver = None

    try:
        driver = create_webdriver()

        ensure_logged_in(EMAIL, PASSWORD)

        print("リロードボタンを押下します")
        click_reloads_selenium()

        all_amount = get_all_amount()
        print("マネーフォワードの口座:")
        pprint(all_amount)

        create_monthly_balance_page = CreateMonthlyBalancePage(
            NOTION_TOKEN, PARENT_PAGE_ID
        )
        current_month_balance = create_monthly_balance_page.main(all_amount)
        print(f"月初の残高: {current_month_balance}")

        current_month_expense = get_current_month_expense()
        current_month_expense_formatted = "{:,}".format(current_month_expense)
        print(f"現在の支出: {current_month_expense_formatted}")

        balance, stock = calculate_balance(
            all_amount, current_month_balance, current_month_expense
        )
        print(f"ラッキーマネー: {balance}\n証券口座:\n{stock}")

        context = (
            f"[ラッキーマネー]\n{balance}\n\n"
            f"[現在の支出]\n{current_month_expense_formatted}\n\n"
            f"[証券口座]\n{stock}"
        )
        print("LineNotifyに純資産の値を送信します")
        send_line_message(context)
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
        print(f"トレースバック: {traceback.format_exc()}")
        line_relay.send_message("ParseMoneyForwardでエラーが発生しました")
        line_relay.send_message(f"エラーが発生しました: {str(e)}")
        line_relay.send_message(f"トレースバック: {traceback.format_exc()}")
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    main()
