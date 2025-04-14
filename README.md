# Web Article Analysis
<img alt="architecture" height="400px" src="https://github.com/kitotakumi/web_article_analysis/blob/main/architecture.png"/>

## 概要

本プロジェクトは、複数の URL を受け取り、並列処理で各 URL の解析を行います。以下の機能を備えています。

- **全体スクリーンショットの取得**  
  Selenium を用いて、指定 URL のページ全体のスクリーンショットを取得します。

- **内容と視覚情報の分析**  
  Gemini API を利用し、取得したスクリーンショットとテキスト情報を基に、視覚的特徴および内容的特徴についての分析を行います。

- **コンテナ化された Lambda 環境**  
  ECR にデプロイすることで、Lambda として実行可能な形にしています。  
  Selenium で利用する Chrome と chromedriver のバージョン管理を正確に行うため、コンテナイメージを用いて Lambda を作成しています。

 - **Lambdaのウォームスタート対策**  
  Lambdaのウォームスタートでは前回の/tmpディレクトリが引き継がれます。残留しているファイルの影響でselemiumが動かなくなることがあるため、/tmpを初期化する関数を最初に実行しています。

- **ホットリロード機能の実装**  
  開発中のホットリロード機能を実装しており、コード変更時に自動で反映されます。

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
