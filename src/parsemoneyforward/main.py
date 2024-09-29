import os
import pickle
import re
import time
import traceback

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from random_user_agent.params import OperatingSystem, SoftwareName
from random_user_agent.user_agent import UserAgent
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv(verbose=True)

COOKIE_FILE = "cookies.pkl"
SCREENSHOT_FILE = "reload_screenshot.png"
global driver
driver = None


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
        if 'domain' in cookie:
            del cookie['domain']
        driver.add_cookie(cookie)


def is_logged_in():
    """Seleniumを使ってログインしているかを判別"""
    url = "https://moneyforward.com/accounts"
    driver.get(url)
    time.sleep(3)  # ページ読み込みを待機
    # 現在のURLが/accountsかを確認
    if driver.current_url == url:
        return True
    return False


def login_selenium(email, password):
    """Seleniumライブラリでログインする

    Args:
        email str: moneyforwordのメールアドレス
        password str: moneyforwordのパスワード
    """

    global driver
    login_url = "https://moneyforward.com/users/sign_in"
    driver.get(login_url)
    time.sleep(3)
    try:
        # Email入力
        email_element = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[@id='mfid_user[email]']")
            )
        )
        email_element.send_keys(email)
        time.sleep(1)

        # [ログインする]ボタン押下(パスワード入力前に必要)
        driver.find_element(by=By.XPATH, value="//*[@id='submitto']").click()
        time.sleep(1)

        # パスワード入力
        password_element = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[@id='mfid_user[password]']")
            )
        )
        password_element.send_keys(password)

        # ログインボタン押下
        driver.find_element(by=By.XPATH, value="//*[@id='submitto']").click()

        # ログイン後、ページが完全に読み込まれるまで待機
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[contains(@href, '/accounts')]")
            )
        )

        time.sleep(3)  # 追加の待機時間

        # 取得したクッキーを保存
        save_cookies(driver, COOKIE_FILE)
        print("ログイン後にクッキーの保存が完了しました。")

    except Exception as e:
        # Seleniumでのログイン失敗
        print(f"Error during login: {e}")
        raise


def click_reloads_selenium():
    """Seleniumを使用して口座の更新ボタンをクリックする"""
    xpath = "//input[@data-disable-with='更新']"
    max_retries = 3

    try:
        # 全ての更新ボタンを見つける
        buttons = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.XPATH, xpath))
        )
        # 各更新ボタンを押下する
        for button in buttons:
            for attempt in range(max_retries):
                try:
                    # ボタンが再度見つかるまで待機
                    refreshed_button = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, xpath))
                    )
                    # クリック可能になるまで待機してクリック
                    WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable(refreshed_button)).click()
                    time.sleep(1)
                    break  # クリック成功したらこのボタンの処理を終了
                # ボタンの押下に失敗した際の処理
                except StaleElementReferenceException:
                    if attempt == max_retries - 1:
                        print(f"ボタン {buttons.index(
                            button) + 1} はクリックできませんでした。")
                    continue  # 次の試行へ

    except Exception as e:
        print(f"更新ボタン押下時にエラーが発生しました: {str(e)}")


def extract_number(text):
    """正規表現でマイナス記号と数字を抽出

    Args:
        text (str): 抽出元の文字列

    Returns:
        int: マッチした場合はその値をそうでない場合は0を格納する
    """
    match = re.search(r'-?\d+,?\d+', text)
    if match:
        # 抽出した値のカンマを除去して整数に変換
        return int(match.group().replace(',', ''))
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
        section = soup.find('section', id='registered-accounts')
        if section:
            li_elements = section.find_all(
                'li', class_=['heading-category-name', 'account'])
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
        if 'heading-category-name' in li['class']:
            heading = li.text.strip()
            if heading not in all_amount:
                all_amount[heading] = []
        elif 'account' in li['class']:
            # 口座名
            bank_name = li.find('a').text
            # 使用高
            amount_ = li.find('ul', class_="amount").find(
                'li', class_="number")
            amount = extract_number(amount_.text) if amount_ else 0
            # 残高
            balance_ = li.find('ul', class_="amount").find(
                'li', class_="balance")
            balance = extract_number(balance_.text) if balance_ else 0

            account_data = {
                'bank_name': bank_name,
                'number': amount,
                'balance': balance
            }

            all_amount[heading].append(account_data)
    return all_amount


def get_notion_database():
    """Notionデータベースの値を取得する

    Returns:
        list: Notionデータベースの値
    """
    database_id = os.environ["NOTION_DATABASE_ID"]
    api_key = os.environ["NOTION_KEY"]
    notion_database = []
    # URLを関数内で定義
    url = f'https://api.notion.com/v1/databases/{database_id}/query'
    headers = {
        "Notion-Version": "2022-06-28",
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(url, headers=headers)
    results = response.json().get('results', [])

    for result in results:
        name = result["properties"]["名前"]['title'][0].get('plain_text', 'N/A')
        price = result["properties"]["数値"].get('number', 'N/A')
        notion_database.append({
            'name': name,
            'price': price
        })
    return notion_database


def get_current_month_balance():
    summary_url = "https://moneyforward.com/cf/summary"
    driver.get(summary_url)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    current_month_balance_ = soup.find(
        'section', id='monthly-total').find('tbody').find_all('td')[-1]
    current_month_balance = extract_number(
        current_month_balance_.text.replace('\n', ''))

    return current_month_balance


def calculate_balance(all_amount, notion_database, current_month_balance):
    balance_list = []
    stock_list = []

    # マネーフォワードの口座
    for category, items in all_amount.items():
        for item in items:
            (stock_list if category == "証券" else balance_list).append({
                'name': item['bank_name'],
                'price': item['number']
            })
    # Notionのデータベース
    for data in notion_database:
        name = data['name']
        price = data['price']
        balance_list.append({
            'name': name,
            'price': price
        })
    # 今月の支出
    balance_list.append({
        'name': '今月の支出',
        'price': current_month_balance
    })

    balance_ = sum(item['price'] for item in balance_list)
    balance = f"{balance_:,}円"
    stock = "\n".join(
        [f"{item['name']}: {item['price']:,}円" for item in stock_list])
    return balance, stock


def send_line_notify(context):
    """LineNotifyでメッセージを送信する

    Args:
        context str: 送信する文字列
    """
    # APIのURLとトークン
    url = "https://notify-api.line.me/api/notify"
    load_dotenv(verbose=True)
    LINE_NOTIFY_TOKEN = os.getenv('LINE_NOTIFY_TOKEN')

    # メッセージを送信
    headers = {"Authorization": "Bearer " + LINE_NOTIFY_TOKEN}
    send_data = {"message": context}
    requests.post(url, headers=headers, data=send_data)


if __name__ == "__main__":
    load_dotenv(verbose=True)
    try:
        # 環境変数の値を読み込む
        email = os.environ["EMAIL"]
        password = os.environ["PASSWORD"]

        chrome_options = Options()
        # ヘッドレスモードで起動する。
        chrome_options.add_argument("--headless=new")
        # ユーザーエージェントの指定。
        software_names = [SoftwareName.CHROME.value]
        operating_systems = [OperatingSystem.WINDOWS.value,
                             OperatingSystem.LINUX.value]
        user_agent_rotator = UserAgent(
            software_names=software_names, operating_systems=operating_systems, limit=100)
        chrome_options.add_argument(
            f"--user-agent={user_agent_rotator.get_random_user_agent()}")
        # ウィンドウの初期サイズを最大化。
        chrome_options.add_argument("--start-maximized")
        driver = webdriver.Chrome(options=chrome_options)

        # クッキーが存在するかを確認
        try:
            cookies = load_cookies(COOKIE_FILE)
            driver.get("https://moneyforward.com")  # クッキーをセットするために一度サイトを開く
            add_cookies_to_driver(driver, cookies)
            print("クッキーをロードしてサイトにアクセスしました。")
        except FileNotFoundError:
            print("クッキーが見つかりません。通常のログインを行います。")
            login_selenium(email, password)

        # ログインチェック
        if not is_logged_in():
            print("クッキーが無効です。通常のログインを実行します。")
            login_selenium(email, password)

        # 口座の更新
        print("リロードボタンを押下します。")
        click_reloads_selenium()

        # Lineに値を送信
        print("LineNotifyに純資産の値を送信します")
        all_amount = get_all_amount()
        print("マネーフォワードの口座\n", all_amount)
        notion_database = get_notion_database()
        print("Notionのデータベース\n", notion_database)
        current_month_balance = get_current_month_balance()
        print("現在の収支\n", current_month_balance)
        balance, stock = calculate_balance(
            all_amount, notion_database, current_month_balance)
        print(f"ラッキーマネー\n{balance}\n証券口座")
        context = f"\n[ラッキーマネー]\n{balance}\n\n[証券口座]\n{stock}"
        send_line_notify(context)

        print("処理が完了しました。")
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
        print(f"トレースバック: {traceback.format_exc()}")
    finally:
        if driver:
            driver.quit()
