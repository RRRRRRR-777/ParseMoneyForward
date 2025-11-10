import datetime
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
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv(verbose=True)

COOKIE_FILE = "cookies.pkl"
SCREENSHOT_FILE = "reload_screenshot.png"
global driver
driver = None

# LogRelayの初期化
line_relay = LineRelay(
    os.getenv("LINE_ACCESS_LOG_RELAY_TOKEN"),
    os.getenv("USER_ID"),
)


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


def get_totp_code():
    """二段階認証コードを生成"""
    totp_secret = os.environ.get("TOTP_SECRET")
    if not totp_secret:
        raise ValueError("TOTP_SECRETが設定されていません")

    totp_secret = totp_secret.replace(" ", "").strip()

    # Base32形式の検証
    try:
        totp = pyotp.TOTP(totp_secret)
        return totp.now()
    except Exception as e:
        raise ValueError(f"TOTP_SECRETの形式が不正です: {e}")


def is_logged_in():
    """
    Seleniumを使用して、ユーザーがログインしているかを確認します。

    指定されたURL（https://moneyforward.com/accounts）にアクセスし、
    ページが/accountsかどうかでログイン状態を判定します。

    Returns:
        bool: ログインしていればTrue、そうでなければFalseを返します。
    """
    url = "https://moneyforward.com/accounts"
    driver.get(url)

    # リダイレクトとJavaScriptレンダリングを待つ
    time.sleep(10)

    # 現在のURLを確認
    current_url = driver.current_url
    print(f"ログイン確認 - 現在のURL: {current_url}")

    # /accountsまたは/にいればログイン成功
    # /sign_in や /email_otp にリダイレクトされたらログイン失敗
    if "/sign_in" in current_url or "/email_otp" in current_url:
        return False
    if "/accounts" in current_url or current_url == "https://moneyforward.com/":
        return True

    return False


def _wait_for_page_load(driver, timeout=60):
    """ページの読み込みとJavaScriptレンダリングを待機"""
    email_element = WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.XPATH, "//input[@type='email']"))
    )
    body_count = len(driver.find_elements(By.XPATH, "//body//*"))
    print(f"ページ読み込み完了 (要素数: {body_count})")
    return email_element


def _handle_page_load_error(driver):
    """ページ読み込み失敗時のエラー処理"""
    body_count = len(driver.find_elements(By.XPATH, "//body//*"))
    driver.save_screenshot("debug_error_page.png")

    print(f"\nログインページ読み込み失敗")
    print(f"Body要素数: {body_count} (正常時: 107-108)")
    print(f"スクリーンショット: debug_error_page.png")
    print(f"原因: JavaScriptレンダリング失敗 (Raspberry Pi環境の制約)")
    print(f"対処: PC環境でcookies.pklを取得してください\n")


def _handle_totp_authentication(driver):
    """TOTP二段階認証を処理"""
    print("TOTP認証開始")
    time.sleep(15)

    # TOTP入力欄を探す
    totp_element = None
    for xpath in ["//input[@type='text']", "//input[@type='tel']", "//input"]:
        try:
            totp_element = WebDriverWait(driver, 5).until(
                EC.visibility_of_element_located((By.XPATH, xpath))
            )
            break
        except:
            continue

    if not totp_element:
        raise Exception("TOTP入力欄が見つかりません")

    # TOTPコード生成と入力
    totp_code = get_totp_code()
    print(f"TOTPコード: {totp_code}")
    totp_element.send_keys(totp_code)
    time.sleep(2)

    # 送信ボタンをクリック
    submit_button = None
    for xpath in ["//button[@type='submit']", "//button"]:
        try:
            submit_button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            break
        except:
            continue

    if not submit_button:
        raise Exception("送信ボタンが見つかりません")

    submit_button.click()
    print("TOTP認証完了")
    time.sleep(5)


def _complete_login_and_save_cookies(driver):
    """ログイン完了確認とクッキー保存

    Args:
        driver: Seleniumドライバー

    Raises:
        Exception: ログイン確認失敗時
    """
    # ログイン後、ページが完全に読み込まれるまで待機
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[contains(@href, '/accounts')]")
        )
    )

    print("ログイン成功。/accountsページに移動します...")

    # /accountsページに実際に移動してからクッキーを保存
    driver.get("https://moneyforward.com/accounts")
    time.sleep(5)  # ページ読み込みとセッション確立を待つ

    # 正常にログインできたか確認
    if "/sign_in" in driver.current_url or "/email_otp" in driver.current_url:
        print(f"警告: ログイン後のリダイレクトが失敗しました。現在のURL: {driver.current_url}")
        raise Exception("ログインに失敗しました")

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
    login_url = "https://moneyforward.com/users/sign_in"

    print("ログインページにアクセスします...")
    driver.get(login_url)

    # ページ読み込みとメール入力欄の検出
    try:
        email_element = _wait_for_page_load(driver)
    except Exception:
        _handle_page_load_error(driver)
        raise

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

    except Exception as e:
        print(f"ログインエラー: {e}")
        raise


def click_reloads_selenium():
    """
    Seleniumを使用して、マネーフォワードの「更新」ボタンを全てクリックします。

    XPATHで「更新」ボタンを取得し、順番にクリックします。エラーが発生した場合には、
    エラーメッセージを表示します。

    Raises:
        Exception: ボタンのクリック中に発生したエラーを表示します。
    """
    try:
        elms = driver.find_elements(
            By.XPATH, "//input[@data-disable-with='更新']")
        for elm in elms:
            elm.click()
            time.sleep(0.5)
    except Exception as e:
        print(f"更新ボタンのクリック中にエラーが発生しました。\n{e}")
    finally:
        time.sleep(3)


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
    toppage_url = "https://moneyforward.com"
    driver.get(toppage_url)

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
    driver.get(summary_url)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    current_month_expense_ = (
        soup.find(
            "section", id="monthly-total").find("tbody").find_all("td")[-1]
    )
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


if __name__ == "__main__":
    load_dotenv(verbose=True)
    try:
        # 環境変数の値を読み込む
        EMAIL = os.environ["EMAIL"]
        PASSWORD = os.environ["PASSWORD"]
        # Notionの認証トークン
        NOTION_TOKEN = os.environ["NOTION_KEY"]
        # 親ページのID
        PARENT_PAGE_ID = os.environ["NOTION_PAGE_ID"]

        chrome_options = Options()
        # ユーザーデータの保存先を一意にする
        unique_dir = f"/tmp/chrome_user_data_{os.getpid()}"
        chrome_options.add_argument(f"--user-data-dir={unique_dir}")
        # ヘッドレスモードで起動する。
        chrome_options.add_argument("--headless=new")
        # ユーザーエージェントの指定。
        software_names = [SoftwareName.CHROME.value]
        operating_systems = [
            OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value]
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
        # Raspberry Piなどのリソース制約環境での安定動作のための追加オプション
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--enable-javascript")
        chrome_options.add_argument("--disable-extensions")
        # ウィンドウの初期サイズを最大化。
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--window-size=1920,1080")
        service = Service(executable_path="/snap/bin/chromium.chromedriver")
        # chromedriverのパスを指定してサービスを作成
        driver = webdriver.Chrome(service=service, options=chrome_options)

        # クッキーベースのログイン試行
        cookie_loaded = False
        try:
            cookies = load_cookies(COOKIE_FILE)
            driver.get("https://moneyforward.com")
            add_cookies_to_driver(driver, cookies)
            print("✓ クッキーをロードしました")
            cookie_loaded = True
        except FileNotFoundError:
            print("\n" + "=" * 80)
            print("【警告】クッキーファイルが見つかりません")
            print("=" * 80)
            print(f"ファイル: {COOKIE_FILE}")
            print()
            print("クッキーなしでログインを試みますが、この環境では")
            print("JavaScriptレンダリングが非常に不安定です。")
            print()
            print("【環境的な制約】")
            print("  • Raspberry Pi (ARM) + Snap Chromium + ヘッドレスモード")
            print("  • MoneyForwardのSPAは完全なJavaScript実行が必要")
            print("  • リソース不足により、ほとんどの場合ログインに失敗します")
            print()
            print("【対処方法】")
            print("  成功率を上げるには、PC環境でログインしてcookies.pklを")
            print("  取得することを強く推奨します。")
            print("  （成功すればクッキーが保存され、次回以降は安定動作）")
            print("=" * 80 + "\n")

        # ログイン状態確認
        if cookie_loaded and is_logged_in():
            print("✓ クッキーでログイン成功")
        else:
            if cookie_loaded:
                print("クッキーが無効です。通常のログインを実行します。")
            else:
                print("クッキーなしでログインを試みます...")
            login_selenium(EMAIL, PASSWORD)

        # 口座の更新
        print("リロードボタンを押下します")
        click_reloads_selenium()

        # Lineに値を送信
        all_amount = get_all_amount()
        print("マネーフォワードの口座:")
        pprint(all_amount)
        # Notionから値を取得
        create_monthly_balance_page = CreateMonthlyBalancePage(
            NOTION_TOKEN, PARENT_PAGE_ID
        )
        current_month_balance = create_monthly_balance_page.main(all_amount)
        print(f"月初の残高: {current_month_balance}")
        # 現在の支出を取得
        current_month_expense = get_current_month_expense()
        current_month_expense_formatted = "{:,}".format(current_month_expense)
        print(f"現在の支出: {current_month_expense_formatted}")
        # 現在の残高を計算
        balance, stock = calculate_balance(
            all_amount, current_month_balance, current_month_expense
        )
        print(f"ラッキーマネー: {balance}\n証券口座:\n{stock}")
        context = f"[ラッキーマネー]\n{balance}\n\n[現在の支出]\n{current_month_expense_formatted}\n\n[証券口座]\n{stock}"
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
