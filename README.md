# Web Article Analysis

複数のURLと動的な分析項目を引数として、並列処理で各URLの分析を行い、スプレッドシートに結果を書き込みます。

## 主要な機能
- **スクリーンショットの取得**
    - Seleniumを用いて記事全体とメインビジュアルのスクリーンショットを取得します。AWS S3の公開URL形式で出力します。
- **記事全体の画像とテキストを文字起こし**
    - Seleniumを用いて取得したHTMLをmarkdownに変換します。記事内に出てくる画像もそれぞれGPT 4.1 nanoを用いて並列処理でテキスト化しています。
- **AIによる記事内容の解析**
    - ユーザーが入力した分析項目について、AIで分析を行います。Gemini 2.0 Flashにmarkdownとスクショを読み込ませています。

## アーキテクチャ
<img alt="architecture" height="400px" src="https://github.com/kitotakumi/web_article_analysis/blob/main/web_article_analysis_architecture.png"/><br>

- **コンテナ化された Lambda 環境**  
  社内アプリでリクエスト数が少ない想定であるため、イベント稼働のLambdaを用いています。  
  コンテナ環境を用いるメリットとしては1. Seleniumで利用するChromeとchromedriverのバージョン管理を正確に行うことができる、 2. コールドスタートの立ち上がりがLambda layerに比べて70%早いの2点があります。

 - **Lambdaの切り分け**  
  大量のURLの並列スクレイピングには大量のリソースが必要になるため、各URLごとのスクレイピング処理を単一のLambdaに切り分け、適切なメモリを割り当てています。またLambdaではメモリ領域の`/dev/shum`の容量を拡大できないため、並列処理によって領域が足りずにchromeがクラッシュすることがあります。そこでchromeの一時ファイルの保存領域をディスク領域の`/tmp`に設定しています。各LambdaはAPIとして他のシステムからも呼び出し可能です。

 - **Seleniumのウォームスタート対策**  
  Lambdaのウォームスタートでは前回の`/tmp`ディレクトリが引き継がれます。残留しているファイルの影響でselemiumが動かなくなるため、`/tmp`を初期化する関数を最初に実行しています。

- **フロントエンド**<br>
base64をやりとりするとデータサイズが大きくなってしまうため、S3のパブリックURLを発行して受け渡しています。AIのレスポンスはjson形式で吐き出させて、スプレッドシートにURLとAIの解答を格納します。分析カラムはユーザーが動的に数・項目を変更できます。

[開発記事はこちら](https://qiita.com/takumi-kito/items/27f4dcceee1c89a4368c)

## フロントエンド
https://github.com/user-attachments/assets/204631e3-f5af-41a4-8b59-029e7d040e6c



## セットアップ
### ローカルで実行
- リポジトリのクローン

```bash
git clone https://github.com/kitotakumi/web_article_analysis.git
cd web_article_analysis
```

- Docker コンテナのビルドと起動<br>
  ymlファイルの環境変数を書き換えてください

```bash
docker compose up --watch
```

### ECR, Lambdaにデプロイ
- AWS ECRにログイン
```bash
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin your_account_id.dkr.ecr.your_region.amazonaws.com
```

- Docker イメージのビルドおよびプッシュ<br>
  platformの指定とprovenance=falseは必須です。
```bash
docker buildx build --platform linux/amd64 --provenance=false --push -t your_repository_url/competitor_analysis:latest .
```
