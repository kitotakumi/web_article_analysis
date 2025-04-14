import json
import requests
import base64
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from tempfile import mkdtemp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_fixed, before_log
import boto3
import logging
import sys
import shutil

# ロガーの設定
logging.basicConfig(stream=sys.stderr,level=logging.DEBUG)
logger = logging.getLogger(__name__)

#実行環境がローカルの場合、LOCAL_ENVを設定する
LOCAL_ENV = os.environ.get("LOCAL_ENV", "false").lower() == "true"

# 関数外で実行することでウォームスタートを活かす
if not LOCAL_ENV:
    s3 = boto3.client('s3')
    S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")

def initialize_lambda_environment():
    # ウォームコンテナの場合、前回の実行結果が /tmp に残っている可能性があるため、全てのファイルとディレクトリを削除します。
    tmp_dir = "/tmp"
    for filename in os.listdir(tmp_dir):
        file_path = os.path.join(tmp_dir, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)  # ファイルまたはシンボリックリンクを削除
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)  # ディレクトリを再帰的に削除
        except Exception as e:
            print(f"Error deleting {file_path}: {e}")

def fetch_html(url):
    """指定のURLからHTML内容を取得する"""
    response = requests.get(url)
    response.raise_for_status()  # ステータスコードが200以外の場合、例外を発生させる
    return response.text


def take_fullpage_screenshot_with_timeout(url, output_path, timeout_seconnds=27):
    # タイムアウトを設定
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(take_fullpage_screenshot, url, output_path)
        return future.result(timeout=timeout_seconnds)

def take_fullpage_screenshot(url, output_path):
    """指定のURLの全体スクリーンショットを取得する"""
    options = webdriver.ChromeOptions()
    service = webdriver.ChromeService("/opt/chromedriver")

    options.binary_location = '/opt/chrome/chrome'
    options.add_argument("--headless=new") #GUIを表示しない。コマンドラインで開く。
    options.add_argument('--no-sandbox') # セキュリティサンドボックスを無効にする。
    options.add_argument("--disable-gpu") # GPUではなくCPUでグラフィック処理
    options.add_argument("--window-size=1280x1696") # 画面サイズを指定
    options.add_argument("--hide-scrollbars") # スクロールバーを非表示にする
    # options.add_argument("--single-process") # 使うと安定性が下がるがリソース消費は減る
    options.add_argument("--disable-dev-shm-usage") #dev/shmはchromeが頻繁に利用する共有メモリ領域。Lambdaではサイズの変更ができず足りなくなる。このオプションを使うと代わりに/tmpを用いるようになる。
    options.add_argument("--disable-dev-tools") #開発ツールを無効にする
    options.add_argument("--no-zygote") #zygoteは新しいレンダラープロセス（タブや拡張機能）を高速生成する。
    options.add_argument(f"--user-data-dir={mkdtemp()}") #一時ディレクトリを生成し、不要なデータの残留を防ぐ
    options.add_argument(f"--data-path={mkdtemp()}")
    options.add_argument(f"--disk-cache-dir={mkdtemp()}")
    # options.add_argument("--remote-debugging-port=9222") #デバッグ用

    try:
        chrome = webdriver.Chrome(options=options, service=service)
        # chrome.implicitly_wait(10) こいつ入れると全然動かなくなる。
        chrome.get(url)
        
        # ページが完全にロードされるまで明示的に待機（bodyタグが表示されるまで）
        WebDriverWait(chrome, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Chrome DevTools Protocol (CDP) を使ってページ全体のサイズを取得
        metrics = chrome.execute_cdp_cmd("Page.getLayoutMetrics", {})
        width = metrics["contentSize"]["width"]
        height = metrics["contentSize"]["height"]
        
        # ウィンドウサイズをページ全体のサイズに合わせる
        chrome.set_window_size(width, height)
        
        # スクリーンショットの保存
        chrome.save_screenshot(output_path)
    except Exception:
        raise
    finally:
        chrome.quit()

#全体スクショをトリミング
def crop_screenshot(screenshot_path):
    # 保存先のpathを設定
    base_name = os.path.basename(screenshot_path)
    name, ext = os.path.splitext(base_name)
    file_path = f"/tmp/{name}_cropped{ext}"
    # 画像をトリミング
    image = Image.open(screenshot_path)
    width, height = image.size
    crop_area = (0,0,width,900)
    cropped_image = image.crop(crop_area)
    cropped_image.save(file_path)
    print("画像をトリミングして保存しました")
    return file_path

def call_jina_reader(url):
    """
    Jina Reader API を呼び出して、URL からテキスト抽出を行う。
    ※URL に対して "https://r.jina.ai/" を先頭に付与して呼び出す形になっています。
    """
    jina_url = "https://r.jina.ai/" + url
    api_key = os.environ.get("JINA_API_KEY")
    headers = {
        "Authorization": "Bearer " + api_key,
        "X-Timeout": "5",
        "X-With-Generated-Alt": "true"
    }
    response = requests.get(jina_url, headers=headers)
    response.raise_for_status()
    return response.text

def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

@retry(stop=stop_after_attempt(2), wait=wait_fixed(2), before=before_log(logger, logging.DEBUG))
def call_gemini(text, screenshot_path):
    """
    Gemini API を呼び出し、Jina Reader によって取得したテキストとスクリーンショット画像を渡して処理を実施。
    """
    API_KEY = os.environ.get("GEMINI_API_KEY")
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"
    
    image_encoded = encode_image_to_base64(screenshot_path)
    
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": "image/png",  # 画像のMIMEタイプ
                            "data": image_encoded,
                        }
                    },
                    {"text": text}
                ]
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(gemini_url, headers=headers, json=payload)
    response.raise_for_status()
    response_json = response.json()
    
    gemini_text = None
    if "candidates" in response_json and len(response_json["candidates"]) > 0:
        gemini_text = response_json["candidates"][0]["content"]["parts"][0]["text"]
    
    return gemini_text, image_encoded

@retry(stop=stop_after_attempt(2), wait=wait_fixed(2), before=before_log(logger, logging.DEBUG))
def call_gemini_no_image(text):
    """
    Gemini API を呼び出し、Jina Reader によって取得したテキストのみを渡して処理を実施。
    """
    API_KEY = os.environ.get("GEMINI_API_KEY")
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": text}
                ]
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(gemini_url, headers=headers, json=payload)
    response.raise_for_status()
    response_json = response.json()
    
    gemini_text = None
    if "candidates" in response_json and len(response_json["candidates"]) > 0:
        gemini_text = response_json["candidates"][0]["content"]["parts"][0]["text"]
    
    return gemini_text

def upload_to_s3(image_path):
    #ローカル環境ではスキップする
    if LOCAL_ENV:
        print("ローカル環境なのでs3へのアップロードは行いません")
        return "no_url"
    else:
        try:
            base_name = os.path.basename(image_path)
            s3_key = f"live/{base_name}"
            s3.upload_file(image_path, S3_BUCKET_NAME, s3_key, ExtraArgs={'ContentType': 'image/png'})
            print("s3へのアップロードが完了しました")
            #公開urlを返却
            return f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{s3_key}"
        except Exception as e:
            print(f"S3へのアップロードに失敗しました: {e}")
            return "no_url"

def process_url(url, query):
    # 一意のIDを生成
    unique_id = str(uuid.uuid4())
    print(f"{unique_id}の処理を開始します")

    # /tmp 以下にファイル保存（Lambda環境での一時ディレクトリ）
    screenshot_path = f"/tmp/screenshot_{unique_id}.png"
    html_path = f"/tmp/page_{unique_id}.html"
    jina_text_path = f"/tmp/jina_text_{unique_id}.text"
    
    try:
        # HTML取得
        html_content = fetch_html(url)
        print("HTMLを取得しました")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as e:
        return {
            "url": url,
            "error": f"HTML取得に失敗しました: {e}"
        }
    
    try:
        # スクリーンショット取得
        exit_picture = True
        take_fullpage_screenshot_with_timeout(url, screenshot_path)
        # 上部をクロップしたスクショを取得
        cropped_screenshot_path = crop_screenshot(screenshot_path)
        print(f"スクリーンショットを '{screenshot_path}' に保存しました。")

    except TimeoutError:
        print("スクリーンショット取得がタイムアウトしました。")
        exit_picture = False
    except Exception as e:
        print(f"スクリーンショット取得に失敗しました: {e}")
        exit_picture = False
    
    try:
        # Jina Reader を使ってURLのテキスト抽出
        # jina_text = call_jina_reader(url)
        jina_text = "demo"
        print("Jina Readerの結果を保存しました")
        with open(jina_text_path, "w", encoding="utf-8") as f:
            f.write(jina_text)
    except Exception as e:
        print(f"Jina Reader 呼び出しに失敗しました: {e}")
        jina_text = html_content
    
    try:
        if exit_picture:
            # Gemini API を呼び出してテキスト処理（画像付き）
            gemini_text , image_encoded = call_gemini(query + "\n#記事内容#\n" + jina_text, screenshot_path)
            # gemini_text = "demo"
        else:
            gemini_text = call_gemini_no_image(query + jina_text)
            # gemini_text = "demo"
        print(f"Gemini回答が生成されました")
    except Exception as e:
        gemini_text = f"Gemini API 呼び出しに失敗しました: {e}"
    
    try:
        if exit_picture:
            image_url = upload_to_s3(screenshot_path)
            cropped_image_url = upload_to_s3(cropped_screenshot_path)
        else:
            image_encoded = "no_image"
            image_url = "can't_get_image"
            cropped_image_url = "can't_get_image"
    except Exception as e:
        print(f"画像url取得に失敗しました: {e}")
        image_encoded = "no_image"
        image_url = "can't_get_image"
        cropped_image_url = "can't_get_image"
    
    return {
        "url": url,
        "screenshot_url" : image_url,
        "cropped_screenshot_url" : cropped_image_url,
        "gemini_text": gemini_text,
    }

def handler(event, context):
    """
    複数のURLを並列処理し、各結果をまとめて返す。
    event の例:
    {
        "urls": ["https://en.wikipedia.org/wiki/Japan", "https://en.wikipedia.org/wiki/United_States"],
        "query": "このwebサイトについて視覚的特徴と情報的特徴を説明してください"
    }
    """
    initialize_lambda_environment()

    urls = event.get("urls")
    query = event.get("query")
    
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {executor.submit(process_url, url, query): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                # API Gatewayのタイムアウトは120s
                result = future.result(timeout=115)
            except Exception as e:
                print(f"エラーが発生しました{e}")
                result = {
                    "url": url,
                    "screenshot_url" : "can't_get_image",
                    "cropped_screenshot_url" : "can't_get_image",
                    "gemini_text": "an_error_occured",
                }
            results.append(result)
    
    return {
        "statusCode": 200,
        "body": json.dumps(results, ensure_ascii=False)
    }
