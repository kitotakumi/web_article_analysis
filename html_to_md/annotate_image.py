import os
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {OPENAI_API_KEY}"
}


def extract_image_urls(data):
    """
    JSON のリストから 'type': 'image' の要素を取得し、URL の重複を排除して返す。
    """
    # set によってユニーク化
    urls = {
        item["src"]
        for item in data
        if item.get("type") == "image" and "src" in item
    }
    return list(urls)

def describe_image_with_gpt4o(image_url ,prompt):
    """
    OpenAI GPT-4o に requests だけでマルチモーダル入力を送り、
    画像の説明文を取得する。
    """
    payload = {
        "model": "gpt-4.1-nano",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text",      "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}}
                ]
            }
        ],
        "max_tokens": 300
    }
    resp = requests.post(OPENAI_API_URL, headers=HEADERS, json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    print(data)
    return data["choices"][0]["message"]["content"]

def annotate_blocks_with_descriptions(blocks, descriptions):
    """
    blocks: [{'type': ..., 'src': ..., 'alt': ...}, ...]
    descriptions: {'https://.../img1.jpg': '画像説明文', ...}
    戻り値: descriptions を alt に追記した blocks（元のオブジェクトをそのまま更新）
    inplace
    """
    for item in blocks:
        if item.get("type") == "image":
            src = item.get("src")
            if src in descriptions:
                orig_alt = item.get("alt", "")
                desc = descriptions[src]
                # alt の末尾に説明文を追加
                item["alt"] = f"{orig_alt} {desc}".strip()
    return blocks

def generate_image_descriptions(json_data):
    """
    data: JSON リスト
    prompt: 画像に対して投げるプロンプト
    max_workers: 同時並列呼び出し数
    戻り値: { url: 説明文, ... }
    """
    urls = extract_image_urls(json_data)
    descriptions = {}
    prompt = "この画像の内容を日本語で説明してください。"

    # ThreadPoolExecutor で並列実行
    with ThreadPoolExecutor(max_workers=50) as executor:
        # future to url のマッピング
        future_to_url = {
            executor.submit(describe_image_with_gpt4o, url, prompt): url
            for url in urls
        }
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                descriptions[url] = future.result()
            except Exception as e:
                descriptions[url] = f"Error: {e}"

    annotated_blocks = annotate_blocks_with_descriptions(json_data, descriptions)

    return annotated_blocks