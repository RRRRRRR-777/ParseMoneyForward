import os
import pickle
import time
import traceback

from dotenv import load_dotenv
from fake_useragent import UserAgent
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
        chrome_options.add_argument(
            "--user-agent=" + UserAgent("windows").chrome)
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

        # 更新をクリックしてスクリーンショットを撮る
        print("リロードボタンを押下しています。")
        click_reloads_selenium()

        print("処理が完了しました。")
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
        print(f"トレースバック: {traceback.format_exc()}")
    finally:
        if driver:
            driver.quit()
