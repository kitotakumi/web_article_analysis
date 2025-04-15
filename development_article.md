# Docker→LambdaでSelemium+Geminiの並列スクレイピングを実装するまでの苦難
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**  *generated with [DocToc](https://github.com/thlorenz/doctoc)*
    - [はじめに](#%E3%81%AF%E3%81%98%E3%82%81%E3%81%AB)
    - [実装意図](#%E5%AE%9F%E8%A3%85%E6%84%8F%E5%9B%B3)
    - [コード構成](#%E3%82%B3%E3%83%BC%E3%83%89%E6%A7%8B%E6%88%90)
    - [Lambda用のDockerコンテナ構築](#lambda%E7%94%A8%E3%81%AEdocker%E3%82%B3%E3%83%B3%E3%83%86%E3%83%8A%E6%A7%8B%E7%AF%89)
    - [ECRへのデプロイ、Lambdaで実行](#ecr%E3%81%B8%E3%81%AE%E3%83%87%E3%83%97%E3%83%AD%E3%82%A4lambda%E3%81%A7%E5%AE%9F%E8%A1%8C)
    - [Lambda上でのSelemiumの安定稼働](#lambda%E4%B8%8A%E3%81%A7%E3%81%AEselemium%E3%81%AE%E5%AE%89%E5%AE%9A%E7%A8%BC%E5%83%8D)
    - [Lambdaのウォームスタート対策](#lambda%E3%81%AE%E3%82%A6%E3%82%A9%E3%83%BC%E3%83%A0%E3%82%B9%E3%82%BF%E3%83%BC%E3%83%88%E5%AF%BE%E7%AD%96)
    - [並列スクレイピングの負荷対策](#%E4%B8%A6%E5%88%97%E3%82%B9%E3%82%AF%E3%83%AC%E3%82%A4%E3%83%94%E3%83%B3%E3%82%B0%E3%81%AE%E8%B2%A0%E8%8D%B7%E5%AF%BE%E7%AD%96)
    - [付録：ホットリロードの実現](#%E4%BB%98%E9%8C%B2%E3%83%9B%E3%83%83%E3%83%88%E3%83%AA%E3%83%AD%E3%83%BC%E3%83%89%E3%81%AE%E5%AE%9F%E7%8F%BE)
    - [まとめ](#%E3%81%BE%E3%81%A8%E3%82%81)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

### はじめに

初学者の学生エンジニアが複数のURLを並列処理で解析するツールを、DockerやSelemium、Lambdaの沼にハマりながら実装した備忘録です。

キーワード：
Selemium、Docker、ECR、ウォームスタート、共有メモリ領域、並列処理、ホットリロード

### 実装意図

社内での他社URLの調査作業を自動化するために、URLとプロンプトを引数として、スクリーンショットとAIによる分析結果を返します。最終的にはGoogleスプレッドシートやSlackから叩くことを想定しています。

ECRのコンテナイメージからLambdaを作成するという方法をとっています。[参考にさせていただいた記事](https://dev.classmethod.jp/articles/aws-cdk-selenium-docker-image-lambda/)

ECSではなくLambdaを選択した理由はそこまでリクエスト数が多くない想定なのでイベント稼働の方がコストパフォーマンスが良いからです。

コンテナイメージではなく、Lambda LayerでSelemiumを利用する方法もありますが、コンテナの方がコールドスタート速度が早いことやchromeとchrome driverのバージョン管理が容易なことからコンテナから作成しています。

[Lambdaのコールドスタート速度についての記事](https://qiita.com/yasuaki9973/items/2ced573029ba2b349569)

### コード構成

ディレクトリ構成は以下です。

```
.
├── Dockerfile
├── README.md
├── docker-compose.yml
├── main.py
└── requirements.txt
```

コードはこちらに載せています。

以下からいくつかのポイントについて解説します。

### Lambda用のDockerコンテナ構築

Dockerfileは以下のようになっています。

```docker
# ----- ビルドステージ -----
FROM --platform=linux/amd64 public.ecr.aws/lambda/python@sha256:63811c90432ba7a9e4de4fe1e9797a48dae0762f1d56cb68636c3d0a7239ff68 as build
RUN dnf install -y unzip && \
    curl -Lo "/tmp/chromedriver-linux64.zip" "https://storage.googleapis.com/chrome-for-testing-public/132.0.6834.159/linux64/chromedriver-linux64.zip" && \
    curl -Lo "/tmp/chrome-linux64.zip" "https://storage.googleapis.com/chrome-for-testing-public/132.0.6834.159/linux64/chrome-linux64.zip" && \
    unzip /tmp/chromedriver-linux64.zip -d /opt/ && \
    unzip /tmp/chrome-linux64.zip -d /opt/

# ----- 最終ステージ -----
FROM --platform=linux/amd64 public.ecr.aws/lambda/python@sha256:63811c90432ba7a9e4de4fe1e9797a48dae0762f1d56cb68636c3d0a7239ff68

# 必要なライブラリのインストール
RUN dnf install -y atk cups-libs gtk3 libXcomposite alsa-lib \
    libXcursor libXdamage libXext libXi libXrandr libXScrnSaver \
    libXtst pango at-spi2-atk libXt xorg-x11-server-Xvfb \
    xorg-x11-xauth dbus-glib dbus-glib-devel nss mesa-libgbm \
    ipa-gothic-fonts

# Pythonパッケージのインストール
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ビルドステージでダウンロードしたChromeとChromeDriverのコピー
COPY --from=build /opt/chrome-linux64 /opt/chrome
COPY --from=build /opt/chromedriver-linux64 /opt/

# アプリケーションコードのコピー
COPY main.py ./

# Lambdaハンドラーのエントリーポイントを指定（main.handler）
CMD [ "main.handler" ]

```

Lambda用のDockerfileにはいくつか注意するポイントがあります。

1. **ビルドステージと最終ステージの分離**
ビルドステージと最終ステージを分離することで最終的なコンテナイメージのサイズを大幅に削減できます。またビルド時間が短縮される効果もあります。
2. **--platform=linux/amd64**
Lambdaの実行環境がLinuxである一方、私のローカル実行環境がMacであるため、これをつけないとLambda上で動かすことができません。Lambdaの実行環境に合わせたコンテナイメージを作ります。
3. **日本語フォントのダウンロード**
ipa-gothic-fontsというフォントをインストールすることでスクリーンショット時に日本語が文字化けすることがなくなります。

### ECRへのデプロイ、Lambdaで実行

ローカルでの動作確認が取れたらLambda上での動作確認を行います。

まずはコンテナイメージをECRにデプロイします。ECRにログインしてから以下を実行します。

```bash
docker buildx build --platform linux/amd64 --provenance=false --push -t yourid.dkr.ecr.ap-northeast-1.amazonaws.com/competitor_analysis:latest .
```

ここでもプラットフォームの指定を行います。

また`--provenance=false`が超重要です。これをつけないとメタデータのイメージが余分に1個生成されてしまい、Lambdaを作成できなくなります。

ここまでできたらLambdaを作成します。コンソールの関数作成からコンテナイメージを選択します。

Lambdaのメモリサイズですが、デフォルトの128MBだと全く動かないので容量を増やしましょう。詳しくは並列スクレイピングの負荷対策の項目で記述します。

コードを更新したときはECRにデプロイするだけじゃなくてLambdaが参照するイメージも更新してあげないといけません。

### Lambda上でのSelemiumの安定稼働

Chromeの実行はかなりのメモリを消費するため、Lambda上での実行のためには省メモリ対策が重要です。スクリーンショットを行う関数は試行錯誤の結果以下のようにしています。

```python
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
```

色々オプションがあって一つ変えるとすぐに動かなくなったりします。女の子以上に繊細な扱いが求められます。正直自分も何が正解なのかわかりませんがとりあえず上記のオプションで動作は安定しています。Lambdaへの十分なメモリとエフェメラルストレージの割り当ても同様に重要です。

### Lambdaのウォームスタート対策

Lambda上でSelemiumを動かす際にはLambdaのウォームスタート対策も重要です。Lambdaがウォームスタートすると`/tmp`ディレクトリが前回から引き継がれます。ここに残っているファイルが悪さしてSelemiumが全然動かなくなったりするので初期化する関数`initialize_lambda_environment()`をプログラムの最初で実行しています。

```python
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
```

[Lambdaのウォームスタートについての記事](https://zenn.dev/mi_01_24fu/books/d91d10985a5a1a/viewer/what_is_a_cold_start_warm_start)

### 並列スクレイピングの負荷対策

私のユースケースだと複数のurlをスクリーンショットしてGeminiで解析するので並列処理によって時間を短縮しています。並列数が増えるにつれてかなりのメモリサイズやエフェメラルストレージサイズが必要になります。これらのサイズが少ないほどスクリーンショットが失敗する確率が上がっていきます。

Lambda上でのSelemium実行はメモリサイズはもちろん、エフェメラルストレージの容量も重要になります。通常Chromeはさまざまなデータを共有メモリ領域の`/dev/shm`に配置しますが、Lambdaではこの領域のサイズを指定できないため、容量不足に陥ります。そこで`"--disable-dev-shm-usage"`オプションによって代わりにディスク領域の`/tmp`にデータを配置するようにしています。そのためエフェメラルストレージの容量が少ないとchromeが頻繁にクラッシュするようになります。

精密な調査はしていませんが、最終的には最大並列処理数であるThreadPoolExecutorのmax_workersは5、メモリは10GB、エフェメラルストレージは5GBを割り当てています。この構成だとほとんどスクリーンショットが失敗することはないです。

### 付録：ホットリロードの実現

Docker初心者であるため、ホットリロードの実現にも大変苦労しました。当初コードの修正をすぐにコンテナに反映することをvolumeやdocker Compose WatchのSyncで実現しようとしていましたがうまくいきませんでした。

初心者なりに色々調べたり試行錯誤した結果、コンテナイメージをリビルドしないと修正が反映されないことがわかりました。理由はDockerfileでCOPY [main.py](http://main.py) ./をしているからどんなにローカルとコンテナを同期しようとしても結局コンテナイメージからスクリプトが実行されてしまうからです。

結局docke-composeのwatch機能のrebuildによってmain.pyの変更を検知するたびにリビルドするという実装を行っています。

```yaml
services:
  competitor_analysis: # サービス名 (コンテナの名前)
    build:
      context: ./
      dockerfile: ./Dockerfile
    image: competitor_analysis:latest # 使用するイメージ名
    ports:
      - "9000:8080" # ポートマッピング
    environment:
      JINA_API_KEY: "a"
      GEMINI_API_KEY: "a"
      LOCAL_ENV: "true"
    volumes:
      - ./tmp:/tmp
    develop:
      watch:
        - action: rebuild
          path: ./main.py
        - action: rebuild
          path: ./Dockerfile
```

コンテナを立ち上げるときは以下のように実行します。

```bash
docker compose up --watch
```

### まとめ

以上で終わりになります。

AI周りの苦労で言うとAPI Gatewayのタイムアウト上限緩和や、結果をスプレッドシート貼り付けるためのS3のパブリックURL発行やAIのレスポンスの構造化など苦労したポイントはいくつかありましたが本記事では割愛しています。

本記事が誰かの助けになっていれば幸いです。