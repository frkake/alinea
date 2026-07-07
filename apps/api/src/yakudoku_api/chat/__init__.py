"""読解チャットのバックエンド(plans/07 §2、docs/05)。

- context_builder: 論文文脈 + 履歴のパッキング(§2.2)
- stream_pipeline: モデル出力 → SSE(`[[ev:n]]` / aside)変換(§2.4、plans/03 §10.3)
- evidence: 根拠アンカーの実在検証と display 導出(§2.5、P1 忠実性)
- prompts: システムプロンプト + 定型アクション(§2.6・§2.7)
"""
