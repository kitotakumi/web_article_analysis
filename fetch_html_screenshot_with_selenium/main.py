import json
import os
from tempfile import mkdtemp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image
import boto3
import shutil
import uuid

#実行環境がローカルの場合、LOCAL_ENVを設定する
LOCAL_ENV = os.environ.get("LOCAL_ENV", "false").lower() == "true"

# 関数外で実行することでウォームスタートを活かす
if not LOCAL_ENV:
    s3 = boto3.client('s3')
    S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")

# ウォームコンテナの場合、前回の実行結果が /tmp に残っている可能性があるため、全てのファイルとディレクトリを削除。
def initialize_lambda_environment():
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

# Chromeドライバの初期化
def init_driver():
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

    return webdriver.Chrome(options=options, service=service)

# スクリーンショットを取得する関数
def take_fullpage_screenshot(chrome, output_path):
    # Chrome DevTools Protocol (CDP) を使ってページ全体のサイズを取得
    metrics = chrome.execute_cdp_cmd("Page.getLayoutMetrics", {})
    width = metrics["contentSize"]["width"]
    height = metrics["contentSize"]["height"]
    
    # ウィンドウサイズをページ全体のサイズに合わせる
    chrome.set_window_size(width, height)
    
    # スクリーンショットの保存
    chrome.save_screenshot(output_path)
    return

# HTMLを取得する関数
def fetch_html(chrome):
    # ─── 全 data-* 属性から src 情報を抽出して即コピー ───
    chrome.execute_script("""
      document.querySelectorAll('img').forEach(img => {
        Object.keys(img.dataset).forEach(key => {
          if (key.toLowerCase().includes('src')) {
            img.src = img.dataset[key];
          }
        });
      });
    """)

    # 見えていない要素を削除
    chrome.execute_script("""
      document.querySelectorAll('*').forEach(el=>{
        const s = window.getComputedStyle(el);
        if (s.display==='none' || s.visibility==='hidden' || el.hidden) {
          el.remove();
        }
      });
    """)

    # 完全に読み込まれた後のHTMLを取得
    html_content = chrome.page_source
    return html_content

# URLを解析する関数
def analysis_url_with_selenium(url, output_path, exit_picture=True):
    """
    ドライバ起動 → URL読み込み → スクショ → HTML取得 → ドライバ終了
    """
    chrome = None
    html = "can't_get_html"
    try:
        chrome = init_driver()
        
        # chrome.implicitly_wait(10) こいつ入れると全然動かなくなる。
        chrome.get(url)
        
        # ページが完全にロードされるまで明示的に待機（bodyタグが表示されるまで）
        WebDriverWait(chrome, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # スクリーンショットの保存
        try:
            take_fullpage_screenshot(chrome, output_path)
            print(f"{url}のスクリーンショット取得が完了しました")
        except TimeoutError as e:
            print(f"{url}のスクリーンショット取得がタイムアウトしました。{e}")
            exit_picture = False
        except Exception as e:
            print(f"{url}のスクリーンショット取得に失敗しました。{e}")
            exit_picture = False

        # HTML取得
        try:
            html = fetch_html(chrome)
            print(f"{url}のhtml取得が完了しました")
        except Exception as e:
            print(f"{url}のhtml取得に失敗しました。{e}")
            html = "can't_get_html"
    
    except Exception as e:
        print(f"{url}の解析に失敗しました。{e}")
        exit_picture = False
        html = "can't_get_html"
    finally:
        chrome.quit()

    return exit_picture, html

#全体スクショをトリミング
def crop_screenshot(screenshot_path, cropped_path):
    # 画像をトリミング
    image = Image.open(screenshot_path)
    width, height = image.size
    crop_area = (0,0,width,900)
    cropped_image = image.crop(crop_area)
    cropped_image.save(cropped_path)
    return

def upload_to_s3(image_path):
    #ローカル環境ではスキップする
    if LOCAL_ENV:
        print("ローカル環境なのでs3へのアップロードは行いません")
        return ("can't_get_image", None)
    else:
        try:
            base_name = os.path.basename(image_path)
            s3_key = f"live/{base_name}"
            s3.upload_file(image_path, S3_BUCKET_NAME, s3_key, ExtraArgs={'ContentType': 'image/png'})
            #公開urlを返却
            return (f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{s3_key}", s3_key)
        except Exception as e:
            print(f"S3へのアップロードに失敗しました: {e}")
            return ("can't_get_image", None)

def handler(event, context):
    initialize_lambda_environment()

    url = event.get("url")

    # /tmp 以下にファイルパスを準備
    unique_id = uuid.uuid4().hex[:5]
    screenshot_path = f"/tmp/screenshot_{unique_id}.png"
    cropped_path    = f"/tmp/cropped_{unique_id}.png"

    # selemiumで解析
    exit_picture, html = analysis_url_with_selenium(url, screenshot_path)

    # HTML 取得が失敗した場合のみ、一度だけリトライ
    if html == "can't_get_html":
        print(f"{url}のhtml取得が失敗したため、リトライします")
        exit_picture, html = analysis_url_with_selenium(url, screenshot_path)

    if exit_picture:
        crop_screenshot(screenshot_path, cropped_path)
    else:
        cropped_path = "can't_get_image"

    # 画像をS3 にアップロード
    try:
        if exit_picture:
            image_url, s3_key = upload_to_s3(screenshot_path)
            cropped_image_url, cropped_s3_key = upload_to_s3(cropped_path)
            print(f"{url}のs3アップロードが完了しました")
        else:
            image_url         = "can't_get_image"
            cropped_image_url = "can't_get_image"
            s3_key = None
    except Exception as e:
        print(f"{url}のs3アップロードに失敗しました: {e}")
        image_url         = "can't_get_image"
        cropped_image_url = "can't_get_image"

    result = {
        "url": url,
        "screenshot_url": image_url,
        "cropped_screenshot_url": cropped_image_url,
        "html": html,
        "screenshot_s3_key": s3_key,
    }

    return {
        "statusCode": 200,
        "body": json.dumps(result, ensure_ascii=False)
    }
