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
    xorg-x11-xauth dbus-glib dbus-glib-devel nss mesa-libgbm

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