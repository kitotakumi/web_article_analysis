FROM --platform=linux/amd64 public.ecr.aws/lambda/python@sha256:63811c90432ba7a9e4de4fe1e9797a48dae0762f1d56cb68636c3d0a7239ff68

# Pythonパッケージのインストール
COPY requirements.txt ./
RUN pip install -r requirements.txt

# アプリケーションコードのコピー
COPY main.py annotate_image.py ./

# Lambdaハンドラーのエントリーポイントを指定（main.handler）
CMD [ "main.handler" ]
