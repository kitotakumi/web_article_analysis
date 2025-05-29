import json
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, NavigableString, Comment

import annotate_image

def preprocess_br(html: str) -> str:
    # <br> <br/> <BR> などをすべて半角スペースに置き換え
    return re.sub(r'(?i)<br\s*/?>', ' ', html)

def html_to_blocks(html, base_url):
    """
    HTML をブロック単位に分解し、Markdown 変換のための中間データ構造を作成する。
    対応要素: h1-h6, ul/ol, table, hr, blockquote, pre, code, strong/b/em/i, a, img,
    input/button/textarea, video/audio, text
    """
    # 改行処理（br を適切に扱う前処理）
    html_without_br = preprocess_br(html)
    soup = BeautifulSoup(html_without_br, 'html.parser')

    # コメント除去
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    # 不要タグ除去
    for bad in soup(['script', 'style', 'noscript', 'iframe', 'svg']):
        bad.decompose()

    blocks = []

    def walk(node):
        # --- 見出し h1-h6 ---
        if node.name and node.name.startswith('h') and len(node.name) == 2 and node.name[1].isdigit():
            level = int(node.name[1])
            text = node.get_text(strip=True)
            if text:
                blocks.append({'type': 'heading', 'level': level, 'text': text})
            return

        # --- リスト ul/ol ---
        if node.name in ('ul', 'ol'):
            ordered = (node.name == 'ol')
            for li in node.find_all('li', recursive=False):
                blocks.append({'type': 'list_item', 'ordered': ordered, 'text': li.get_text(strip=True)})
            return

        # --- テーブル ---
        if node.name == 'table':
            rows = []
            for section in node.find_all(['thead','tbody'], recursive=False):
                for tr in section.find_all('tr', recursive=False):
                    cells = [cell.get_text(strip=True) for cell in tr.find_all(['th','td'], recursive=False)]
                    rows.append(cells)
            if not rows:
                for tr in node.find_all('tr'):  # 再帰的に全 tr を拾うフォールバック
                    cells = [cell.get_text(strip=True) for cell in tr.find_all(['th','td'], recursive=False)]
                    rows.append(cells)
            blocks.append({'type': 'table', 'rows': rows})
            return

        # --- 水平線 hr ---
        if node.name == 'hr':
            blocks.append({'type': 'hr'})
            return

        # --- 引用 blockquote ---
        if node.name == 'blockquote':
            text = node.get_text(strip=True)
            blocks.append({'type': 'blockquote', 'text': text})
            return

        # --- コードブロック pre ---
        if node.name == 'pre':
            code = node.get_text()
            blocks.append({'type': 'code_block', 'code': code})
            return

        # --- インラインコード code ---
        if node.name == 'code' and not node.find_all():
            blocks.append({'type': 'inline_code', 'text': node.get_text(strip=True)})
            return

        # --- 強調 / 太字 ---
        if node.name in ('strong', 'b', 'em', 'i'):
            text = node.get_text(strip=True)
            style = 'bold' if node.name in ('strong', 'b') else 'italic'
            blocks.append({'type': style, 'text': text})
            return

        # --- リンク a ---
        if node.name == 'a' and node.get('href'):
            href = urljoin(base_url, node['href'])
            # (1) 先に中の画像をすべてブロック化
            for img in node.find_all('img'):
                src = urljoin(base_url, img['src'])
                blocks.append({'type': 'image', 'src': src, 'alt': img.get('alt','')})
            # (2) リンクテキストをブロック化
            text = node.get_text(strip=True)
            if text:
                blocks.append({'type': 'link', 'href': href, 'text': text})
            return


        # # --- ソース要素 source (srcset用) ---
        # if node.name == 'source' and node.get('srcset'):
        #     blocks.append({
        #         'type': 'image',
        #         'src': urljoin(base_url, node['srcset']),
        #         'alt': ''
        #     })
        #     return

        # --- 画像 img ---
        if node.name == 'img' and node.get('src'):
            src = urljoin(base_url, node['src'])
            classes = node.get('class', [])
            # --- sp 版は、同じ親に pc があればスキップ ---
            if 'sp' in classes:
                parent = node.parent
                # 親内に class に 'pc' を含む img がいれば無視
                if parent.find('img', class_=lambda c: c and 'pc' in c.split()):
                    return
            blocks.append({'type': 'image', 'src': src, 'alt': node.get('alt', '')})
            return

        # --- フォーム要素 ---
        if node.name == 'input' and node.get('value'):
            blocks.append({'type': 'text', 'tag': 'input', 'text': node['value']})
            return
        if node.name == 'button':
            blocks.append({'type': 'text', 'tag': 'button', 'text': node.get_text(strip=True)})
            return
        if node.name == 'textarea':
            blocks.append({'type': 'text', 'tag': 'textarea', 'text': node.get_text(strip=True)})
            return

        # --- メディア要素 video/audio ---
        if node.name in ('video', 'audio') and node.get('src'):
            src = urljoin(base_url, node['src'])
            blocks.append({'type': 'media', 'tag': node.name, 'src': src})
            return

        # --- テキスト葉 ---
        if isinstance(node, NavigableString):
            text = node.strip()
            if text and node.parent.name not in ('html', 'body', 'head'):
                blocks.append({'type': 'text', 'tag': node.parent.name, 'text': text})
            return

        # --- 再帰 ---
        for child in node.children:
            walk(child)

    # 本体走査
    if soup.body:
        walk(soup.body)

    return blocks


def blocks_to_markdown(blocks):
    md = []
    for b in blocks:
        t = b['type']
        if t == 'heading':
            md.append('#' * b['level'] + ' ' + b['text'])
        elif t == 'text':
            md.append(b['text'])
        elif t == 'bold':
            md.append('**' + b['text'] + '**')
        elif t == 'italic':
            md.append('*' + b['text'] + '*')
        elif t == 'inline_code':
            md.append('`' + b['text'] + '`')
        elif t == 'code_block':
            md.append('```')
            md.append(b['code'])
            md.append('```')
        elif t == 'blockquote':
            md.append('> ' + b['text'])
        elif t == 'hr':
            md.append('---')
        elif t == 'link':
            md.append(f"[{b['text']}]({b['href']})")
        elif t == 'image':
            md.append(f"![{b['alt']}]({b['src']})")
        elif t == 'list_item':
            prefix = '1.' if b['ordered'] else '-'
            md.append(f"{prefix} {b['text']}")
        elif t == 'table':
            rows = b['rows']
            if not rows:
                continue
            # header
            header = rows[0]
            md.append('| ' + ' | '.join(header) + ' |')
            md.append('| ' + ' | '.join(['---'] * len(header)) + ' |')
            for row in rows[1:]:
                md.append('| ' + ' | '.join(row) + ' |')
        elif t == 'media':
            md.append(f"[{b['tag'].upper()}]({b['src']})")
        md.append('')
    # 末尾空行削除
    while md and md[-1] == '':
        md.pop()
    return '\n'.join(md)

def handler(event, context):
    if "body" in event:
        event = json.loads(event["body"])
    
    html = event.get('html', '')
    base_url = event.get('url', '')
    print(f"{base_url}の処理を開始します")

    # HTMLをブロック化
    try:
        blocks_json = html_to_blocks(html, base_url)
        print(f"{base_url}のHTMLをブロック化しました")
    except Exception as e:
        print(f"html_to_blocks error: {e}")
        return _error_response("Failed to parse HTML")


    # 画像の説明を生成
    try:
        annotated_blocks = annotate_image.generate_image_descriptions(blocks_json)
    except Exception as e:
        print(f"annotate_image error: {e}")
        # フォールバックで元のブロックをそのまま使う
        annotated_blocks = blocks_json

    # markdown 変換
    try:
        markdown = blocks_to_markdown(annotated_blocks)
        print(f"{base_url}のMarkdown 変換が完了しました")
    except Exception as e:
        print(f"blocks_to_markdown error: {e}")
        markdown = "#RAW HTML FALLBACK\n" + html

    body = {
        'blocks_json': annotated_blocks,
        'markdown': markdown
    }

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, ensure_ascii=False)
    }

def _error_response(msg):
    return {
        'statusCode': 500,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps({'error': msg}, ensure_ascii=False)
    }
