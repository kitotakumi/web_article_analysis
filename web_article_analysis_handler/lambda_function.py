import os
import json
import boto3
import requests
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time

API_KEY = os.environ['GEMINI_API_KEY']

# AWSのクライアントを初期化
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
dynamodb_client = boto3.resource('dynamodb')
table = dynamodb_client.Table(os.environ.get("DYNAMODB_TABLE_NAME"))

# Lambdaを呼び出し
def invoke_lambda(fn_name, payload):
    try:
        resp = lambda_client.invoke(
            FunctionName=fn_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        body = resp['Payload'].read().decode('utf-8')
        return json.loads(body)
    except Exception as e:
        print(f"Lambda function {fn_name} invocation failed: {e}")
        raise Exception(f"Error invoking Lambda: {str(e)}") from e

# S3から画像を取得し、base64エンコードする
def fetch_image_s3(s3_key, url):
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        content = obj['Body'].read()
        b64 = base64.b64encode(content).decode('utf-8')
        return b64
    except Exception as e:
        print(f"S3からの画像取得に失敗しました: {e} for {url}")
        return None

def retry_request(func, *args, **kwargs):
    max_retries = 3
    delay = 1
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Retrying request due to error: {e}")
                time.sleep(delay)
            else:
                print(f"Max retries exceeded for request: {e}")
                raise

# Gemini APIを呼び出す（画像あり）
def call_gemini_with_image(text, b64):
    def _inner(text, b64):
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",  # 画像のMIMEタイプ
                                "data": b64,
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
        
        return gemini_text
    
    return retry_request(_inner, text, b64)

# Gemini APIを呼び出す（画像なし）
def call_gemini_no_image(text):
    def _inner(text):
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
    return retry_request(_inner, text)

# DynamoDBにログを記録
def log_to_dynamodb(url, gemini_text, userid):
    try:
        timestamp = datetime.now().isoformat()
        item = {
            "userid": userid,
            "ts": timestamp,
            "url": url,
            "gemini_text": gemini_text
        }
        table.put_item(Item=item)
        print(f"DynamoDBへのログが完了しました for {url}")
    except Exception as e:
        print(f"DynamoDBへのログに失敗しました: {e} for {url}")

# URLを処理する関数
def process_single_url(url, query, userid):
    # 1. Lambdaを呼び出してHTML＋スクショ取得
    html_resp = invoke_lambda("fetch_html_screenshot_with_selenium", {"url": url})
    if isinstance(html_resp.get('body'), str):
        html_resp = json.loads(html_resp['body'])

    html            = html_resp.get('html')
    screenshot_url  = html_resp.get('screenshot_url')
    cropped_url     = html_resp.get('cropped_screenshot_url')
    s3_key          = html_resp.get('screenshot_s3_key')

    # --- 早期リターン: HTML が取れていなければ以降をスキップ ---
    if not html or html == "can't_get_html":
        print(f"{url}：HTML取得に失敗したので処理を中断します")
        return {
            "url": url,
            "screenshot_url":         screenshot_url or "can't_get_image",
            "cropped_screenshot_url": cropped_url    or "can't_get_image",
            "markdown":               "can't_get_markdown",
            "gemini_text":            "can't_get_gemini",
            "error":                  "can't_get_html"
        }

    print(f"HTML and screenshot fetched for {url}")
    print(f"HTML response for {url}: {html_resp}")
    print(f"Screenshot URL for {url}: {screenshot_url}")

    # 2) Lambdaを呼び出してHTMLをMarkdownに変換
    try:
        md_resp = invoke_lambda("html_to_md", {
            "url": url,
            "html": html_resp['html']
        })
        if isinstance(md_resp.get('body'), str):
            md_resp = json.loads(md_resp['body'])
        markdown = md_resp.get('markdown')

        print(f"Markdown conversion completed for {url}")
        print(f"Markdown response for {url}: {md_resp}")
    except Exception as e:
        markdown = "#RAW HTML FALLBACK\n" + html
        print(f"MD変換が失敗したのでHTML情報を格納します。Markdown conversion failed for {url}: {e}")

    # 3) S3から画像を取得
    screenshot_b64 = fetch_image_s3(s3_key, url)

    # 4) Gemini APIを呼び出してテキスト生成
    try:
        query = query + "\n#記事内容#\n" + markdown
        # 画像がある場合はbase64エンコードしたものを渡す、ない場合は画像なしで呼び出す
        if not screenshot_b64:
            gemini_text = call_gemini_no_image(query)
        else:
            gemini_text = call_gemini_with_image(query, screenshot_b64)
        print(f"Gemini text generated for {url}")
    except Exception as e:
        gemini_text = f"Gemini call failed: {e} for {url}"
        print(f"Gemini API call failed for {url}: {e}")
    
    # 5) DynamoDBにログを記録
    log_to_dynamodb(url, gemini_text, userid)

    # 4) マージして返却
    return {
        "url": url,
        "screenshot_url":         screenshot_url,
        "cropped_screenshot_url": cropped_url,
        "markdown":              markdown,
        "gemini_text":           gemini_text,
    }

def lambda_handler(event, context):
    urls   = event.get('urls', [])
    query  = event.get('query', '')
    userid = event.get('userid', 'guest')

    results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(process_single_url, url, query, userid): url
            for url in urls
        }
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({
                    "url": url,
                    "screenshot_url": "can't_get_image",
                    "cropped_screenshot_url": "can't_get_image",
                    "markdown": "can't_get_html",
                    "gemini_text": "can't_get_gemini",
                    "error": str(e)
                })

    return {
        "statusCode": 200,
        "body": json.dumps(results, ensure_ascii=False)
    }
