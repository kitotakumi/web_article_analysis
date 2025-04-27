# Web Article Analysis
<img alt="architecture" height="400px" src="https://github.com/kitotakumi/web_article_analysis/blob/main/architecture.png"/><br>

[開発記事はこちら](https://qiita.com/takumi-kito/items/27f4dcceee1c89a4368c)
## 概要
本プロジェクトは、複数の URL を受け取り、並列処理で各 URL の解析を行います。主要機能は以下です。

- **技術選定**<br>
  EC2やECSではなくLambdaを選定した理由：社内アプリのため、リクエスト数が多くない想定なのでエベント稼動のほうがコスパがいい。<br>
  Lambda LayerではなくコンテナからLambdaを作成している理由：chromeのバージョン管理の容易さとコールドスタートの速さ

- **全体スクリーンショットの取得**  
  Selenium を用いて、指定 URL のページ全体のスクリーンショットを取得します。

- **内容と視覚情報の分析**  
  Gemini API を利用し、取得したスクリーンショットとテキスト情報を基に、視覚的特徴および内容的特徴についての分析を行います。

- **コンテナ化された Lambda 環境**  
  ECR にデプロイすることで、Lambda として実行可能な形にしています。  
  コンテナ環境を用いるメリットとしては1. Seleniumで利用するChromeとchromedriverのバージョン管理を正確に行うことができる、 2. コールドスタートの立ち上がりがLambda layerに比べて70%早いの2点があります。

 - **ウォームスタート対策**  
  Lambdaのウォームスタートでは前回の`/tmp`ディレクトリが引き継がれます。残留しているファイルの影響でselemiumが動かなくなるため、`/tmp`を初期化する関数を最初に実行しています。

 - **負荷対策**  
  大量のURLの並列スクレイピングには大量のリソースが必要になるため、適切にリソース設定を行っています。最大並列処理数は5と設定し、Lambdaのメモリサイズは10GB、エフェメラルストレージは5GBを与えています。またLambdaではメモリ領域の`/dev/shum`の容量を拡大できないため、並列処理によって領域が足りずにchromeがクラッシュすることがあります。そこでchromeの一時ファイルの保存領域をディスク領域の`/tmp`に設定しています。

- **フロントエンド**<br>
base64をやりとりするとデータサイズが大きくなってしまうため、S3のパブリックURLを発行して受け渡しています。AIのレスポンスはjson形式で吐き出させて、スプレッドシートにURLとAIの解答を格納します。分析カラムはユーザーが動的に数・項目を変更できます。

- **ホットリロード機能の実装**  
  開発中のホットリロード機能を実装しており、コード変更時に自動で反映されます。


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
