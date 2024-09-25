import os
import pickle
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


def get_net_assets():
    """純資産の取得

    Returns:
        str: 純資産の値
    """
    # バランスシートページへ遷移
    balance_sheet_url = "https://moneyforward.com/bs/balance_sheet"
    driver.get(balance_sheet_url)

    # 純資産の値を取得
    net_assets_element = WebDriverWait(driver, 30).until(
        EC.presence_of_element_located(
            (By.XPATH,
             "//th[text()='純資産']/following-sibling::td[@class='number']")
        )
    )
    return net_assets_element.text


def get_all_amount():
    """すべての口座の値を取得

    Returns:
        str: 口座の値
    """
    toppage_url = "https://moneyforward.com"
    driver.get(toppage_url)

    # Beautiful Soupでパース
    soup = BeautifulSoup(driver.page_source, "html.parser")
    li_elements = soup.find('section', id='registered-accounts').find_all('li',
                                                                          class_=['heading-category-name', 'account'])
    # 出力を格納する辞書
    all_aomount = {}
    # 各liタグを処理
    for li in li_elements:
        if 'heading-category-name' in li['class']:
            heading = li.text.strip()
            if heading not in all_aomount:
                all_aomount[heading] = []
        elif 'account' in li['class']:
            bank_name = li.find('a').text
            amount = li.find('ul', class_="amount").find(
                'li', class_="number").text
            balance = li.find('ul', class_="amount").find(
                'li', class_="balance").text if li.find('ul', class_="amount").find('li', class_="balance") else None

            account_data = {
                'bank_name': bank_name,
                'number': amount,
                'balance': balance
            }

            all_aomount[heading].append(account_data)
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

        # 純資産の取得
        net_assets = get_net_assets()
        # LineNotifyに純資産の値を送信
        print("LineNotifyに純資産の値を送信します")
        all_amount = get_all_amount()
        notion_database = get_notion_database()

        context = f"\n[すべての口座]\n{all_amount}\n\n[純資産]\n{net_assets}"
        send_line_notify(context)

        print("処理が完了しました。")
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
        print(f"トレースバック: {traceback.format_exc()}")
    finally:
        if driver:
            driver.quit()

"""
[ParseMoneyForward]
[すべての口座]
---銀行---
三井住友銀行
222,669円
楽天銀行
49,007円
---証券---
マネックス
167,133円
楽天証券
301,456円
---カード---
エポスカード
-95,000円
三井住友カード
-161,918円

[純資産]
482,691円
"""
