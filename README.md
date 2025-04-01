### READMEは現在作成途中です
<img alt="architecture" height="400px" src="https://github.com/kitotakumi/web_article_analysis/blob/main/architecture_.png"/>
複数のurlを受け取り、並列処理でurlの分析を行います。<br>
selemiumを用いたスクレイピングでページ全体のスクリーンショットの取得を行います。<br>
geminiに視覚的情報と内容的情報について分析させます。<br>
ECRにデプロイすることでLambdaとして実行可能です。<br>
スクレイピングに使用しているselemiumというライブラリはchromeとchromedriverのバージョン管理が重要であるため、コンテナイメージを用いてlambdaを作成しています<br>
