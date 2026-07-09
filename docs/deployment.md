# Alinea — グローバルホスティング手順書

> 本書は plans/01 §8(環境とデプロイ)を実際の運用手順に落とした手順書である。ローカル完全実装(dev)は README の手順で動作する。本書は **単一 VPS + Docker Compose + Cloudflare R2** で `https://alinea.app` に公開するための全手順を記す。構成の設計判断(単一オリジン・ステージング無し等)は plans/01 §8 を正とする。

## 0. 構成サマリ

- **サーバ**: 単一 VPS(目安: 4 vCPU / 8GB RAM / 160GB SSD。Hetzner CX42 / Lightsail 8GB 相当)
- **オリジン**: `https://alinea.app` 単一オリジン(Caddy が `/api/*` → FastAPI、それ以外 → Next.js に振り分け)。CORS・SameSite 例外・プリフライトが不要になる(plans/01 §8.2)
- **コンテナ**: caddy / web / api / worker-interactive / worker-bulk / postgres(PGroonga)/ redis / prometheus / grafana
- **オブジェクトストレージ**: Cloudflare R2(prod)。MinIO は dev のみ
- **監視**: Prometheus + Grafana(`grafana.alinea.app`、basicauth 二段)+ Sentry + UptimeRobot
- **ステージング環境は置かない**(決定)。deploy 前に dev で同一 compose を再現し `alembic upgrade head` を検証する

## 1. 事前準備(初回のみ)

### 1.1 DNS・ドメイン

1. `alinea.app` の A/AAAA レコードを VPS の IP へ向ける。
2. `grafana.alinea.app` も同一 IP へ(Caddy が SNI で振り分け)。
3. Caddy が Let's Encrypt で自動的に TLS 証明書を取得する(80/443 開放が必要)。

### 1.2 VPS 初期設定

```bash
# Docker + Compose plugin
curl -fsSL https://get.docker.com | sh
# ファイアウォール: 22(SSH)/80/443 のみ開放
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable
# デプロイ用ディレクトリ
mkdir -p /opt/alinea && cd /opt/alinea
```

### 1.3 Cloudflare R2

1. R2 で 2 バケットを作成: `alinea-sources`(原本: LaTeX tar・PDF)/ `alinea-assets`(派生物: 図・サムネ・概要図 SVG・解説図)。
2. R2 API トークン(Object Read & Write)を発行し、`S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` に設定。
3. `S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com`、`S3_REGION=auto`。
4. バックアップ保管用に `backups/` プレフィックスを同アカウントに用意(§6)。

### 1.4 OAuth リダイレクト URI 登録

| プロバイダ | 登録先 | リダイレクト URI |
|---|---|---|
| Google | Google Cloud Console → OAuth クライアント ID(ウェブアプリ) | `https://alinea.app/api/auth/oauth/google/callback` |
| GitHub | GitHub Developer Settings → OAuth Apps | `https://alinea.app/api/auth/oauth/github/callback` |

発行された client id / secret を `OAUTH_GOOGLE_*` / `OAUTH_GITHUB_*` へ。

### 1.5 SMTP(メールマジックリンク)

Amazon SES(または同等の SMTP サービス)を使用:

1. SES でドメイン `alinea.app` を検証(DKIM/SPF レコードを DNS に追加)。
2. サンドボックス解除申請(本番送信)。
3. SMTP 認証情報を発行し `SMTP_HOST=email-smtp.<region>.amazonaws.com` / `SMTP_PORT=587` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM=login@alinea.app` へ。

### 1.6 Sentry・UptimeRobot

- Sentry プロジェクトを api+worker / web の 2 面で作成し `SENTRY_DSN_API` / `SENTRY_DSN_WEB` を取得(`traces_sample_rate=0.1`)。
- UptimeRobot で `https://alinea.app/api/healthz` を 5 分間隔の外形監視に登録。

## 2. 環境変数チェックリスト(.env.production)

`.env.example` を元に `/opt/alinea/.env.production` を作成する。**dev との差分**は以下(全変数の意味は `.env.example` のコメントと plans/01 §8.4 を正とする):

| 変数 | prod 値 |
|---|---|
| `APP_ENV` | `production` |
| `APP_BASE_URL` | `https://alinea.app` |
| `API_BASE_URL` | `https://alinea.app`(単一オリジン) |
| `API_INTERNAL_URL` | `http://api:8000`(compose 内部名) |
| `DATABASE_URL` | `postgresql+asyncpg://alinea:<強パスワード>@postgres:5432/alinea` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `S3_ENDPOINT_URL` | `https://<accountid>.r2.cloudflarestorage.com` |
| `S3_PUBLIC_ENDPOINT_URL` | 未設定(=ENDPOINT と同一) |
| `S3_REGION` | `auto` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | R2 API トークン |
| `SESSION_SECRET` | `openssl rand -hex 32` で生成 |
| `ALINEA_KEY_ENCRYPTION_SECRET` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。ローテーション時はカンマ区切りで新キーを先頭に追加 |
| `OAUTH_*` | §1.4 の値 |
| `SMTP_*` | §1.5 の値 |
| `EXTENSION_ALLOWED_ORIGINS` | `chrome-extension://<ChromeストアID>,chrome-extension://<EdgeストアID>`(ストア公開後に確定 — §8) |
| `OPENAI_API_KEY` ほか運営 LLM キー | 運用キー(未設定プロバイダはルーティングから自動除外) |
| `ALINEA_*_BASE_URL` | **全て空**(モック差し替えは CI/E2E 専用) |
| `SENTRY_DSN_API` / `SENTRY_DSN_WEB` | §1.6 の値 |
| `ARXIV_USER_AGENT` | `alinea/1.0 (contact: admin@alinea.app)` |
| `ALINEA_TEXLIVE_IMAGE` | `alinea-texlive-ja:latest`。LaTeX 由来論文の日本語PDFビルド用 |

> **注意**: 秘匿値に `NEXT_PUBLIC_` / `WXT_` 接頭辞を付けない(CI の grep 検査対象)。LLM のモデル ID・ルーティングは環境変数ではなく DB の設定テーブルで管理し、管理 UI /シードで変更する(再デプロイ不要 — docs/09 §3.2)。

LaTeX PDF ビルドを有効にするホストでは、worker が参照する Docker イメージを事前に作成する:

```bash
docker build -f docker/texlive/Dockerfile -t alinea-texlive-ja:latest .
```

worker コンテナ内からビルドする構成では Docker socket を worker に渡すか、同等のコンテナ実行基盤を用意する。worker は `--pull never` で実行するため、未ビルドの環境では日本語PDF生成だけが警告でスキップされる。

## 3. prod Docker Compose と Caddyfile

`/opt/alinea/docker-compose.prod.yml`(リポジトリの `docker/prod/docker-compose.prod.yml` を配置。plans/01 §8.3 の構成):

```yaml
services:
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    restart: unless-stopped
  web:
    image: ghcr.io/<org>/alinea-web:latest        # next build --output standalone
    env_file: .env.production
    restart: unless-stopped
  api:
    image: ghcr.io/<org>/alinea-api:latest
    command: uvicorn alinea_api.main:app --host 0.0.0.0 --port 8000 --workers 2
    env_file: .env.production
    depends_on: [postgres, redis]
    restart: unless-stopped
  worker-interactive:
    image: ghcr.io/<org>/alinea-worker:latest
    command: arq alinea_worker.main.InteractiveWorker
    env_file: .env.production
    depends_on: [postgres, redis]
    restart: unless-stopped
  worker-bulk:
    image: ghcr.io/<org>/alinea-worker:latest
    command: arq alinea_worker.main.BulkWorker
    env_file: .env.production
    depends_on: [postgres, redis]
    restart: unless-stopped
  migrate:                                          # one-shot(§5 のデプロイ順序で明示実行)
    image: ghcr.io/<org>/alinea-api:latest
    command: alembic upgrade head
    env_file: .env.production
    profiles: [migrate]
    depends_on: [postgres]
  postgres:
    image: groonga/pgroonga:4.0.1-debian-16
    environment:
      POSTGRES_USER: alinea
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: alinea
    volumes: [pgdata:/var/lib/postgresql/data]
    restart: unless-stopped
  redis:
    image: redis:7.4-alpine
    command: redis-server --appendonly yes
    volumes: [redisdata:/data]
    restart: unless-stopped
  prometheus:
    image: prom/prometheus:v2.53.0
    volumes: [./prometheus.yml:/etc/prometheus/prometheus.yml:ro, promdata:/prometheus]
    restart: unless-stopped
  grafana:
    image: grafana/grafana:11.1.0
    volumes: [grafanadata:/var/lib/grafana]
    restart: unless-stopped
volumes: { pgdata: {}, redisdata: {}, caddy_data: {}, promdata: {}, grafanadata: {} }
```

`/opt/alinea/Caddyfile`(plans/01 §8.2 の逐語):

```caddyfile
alinea.app {
    encode zstd gzip
    @api path /api/*
    handle @api {
        reverse_proxy api:8000 {
            flush_interval -1        # SSE 即時フラッシュ
        }
    }
    handle {
        reverse_proxy web:3000
    }
}

grafana.alinea.app {
    basicauth {
        admin <bcrypt ハッシュ>      # caddy hash-password で生成
    }
    reverse_proxy grafana:3000
}
```

全コンテナのログは json-file ドライバでローテーション(`max-size: 50m, max-file: 5` — plans/01 §9.3)。

## 4. イメージビルドと GHCR push(CI)

`.github/workflows/deploy.yml`(タグ push で発火)が以下を行う:

1. `docker build -f docker/prod/Dockerfile.web -t ghcr.io/<org>/alinea-web:<tag>` — Next.js `output: "standalone"` ビルド
2. `docker build -f docker/prod/Dockerfile.api -t ghcr.io/<org>/alinea-api:<tag>` — uv で api+py-core+llm をインストール
3. `docker build -f docker/prod/Dockerfile.worker -t ghcr.io/<org>/alinea-worker:<tag>`
4. `docker push`(GHCR。`GITHUB_TOKEN` の packages:write)
5. `latest` タグを更新

手動で行う場合はローカルで同コマンドを実行し `docker login ghcr.io` 後に push する。

## 5. デプロイ手順(毎回)

**順序が重要**(plans/01 §8.3): マイグレーション → 新コンテナ起動 → 旧停止。

```bash
cd /opt/alinea
docker compose -f docker-compose.prod.yml pull

# 1. DB マイグレーション(one-shot。api 起動前)
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrate

# 2. ローリング再起動(web/api は新→旧の順。SSE 切断はクライアント自動再接続で吸収)
docker compose -f docker-compose.prod.yml up -d

# 3. 動作確認
curl -fsS https://alinea.app/api/healthz   # {"status":"ok"}
curl -fsS https://alinea.app/api/readyz    # PG・Redis・S3 疎通
```

初回のみ追加で:

```bash
# LLM ルーティングの既定値シード(docs/09 §3.4 の表)+ サンプル論文(任意)
docker compose -f docker-compose.prod.yml run --rm api python -m alinea_api.seed --routing-only
```

ロールバック: `docker compose ... up -d` を旧タグ指定で再実行(イメージタグを固定運用)。DB マイグレーションは前方互換に作る(逸脱修正のみの方針 — plans/13 §1.4)ため原則ロールバック不要。

## 6. バックアップ / リストア

**バックアップ**(毎日 03:00 JST、VPS の cron):

```bash
# /etc/cron.d/alinea-backup
0 3 * * * root /opt/alinea/backup.sh
```

`backup.sh`:

```bash
#!/bin/bash
set -euo pipefail
DATE=$(date +%F)
docker compose -f /opt/alinea/docker-compose.prod.yml exec -T postgres \
  pg_dump -U alinea -Fc alinea | zstd > /tmp/pg-$DATE.dump.zst
# R2 へ(aws cli は R2 endpoint 指定で使用)
aws s3 cp /tmp/pg-$DATE.dump.zst s3://alinea-backups/backups/pg/$DATE.dump.zst \
  --endpoint-url https://<accountid>.r2.cloudflarestorage.com
rm /tmp/pg-$DATE.dump.zst
# 30 日より古いものを削除(ライフサイクルルールでも可)
```

- Redis は AOF 永続化のみ(消失してもジョブは PostgreSQL `jobs` テーブルから `python -m alinea_core.jobs.requeue` で再 enqueue 可能 — plans/01 §4.1)。
- R2(原本・派生物)は R2 側の冗長性に委ねる。

**リストア**:

```bash
aws s3 cp s3://alinea-backups/backups/pg/<DATE>.dump.zst - \
  --endpoint-url https://<accountid>.r2.cloudflarestorage.com | zstd -d > /tmp/restore.dump
docker compose -f docker-compose.prod.yml stop api worker-interactive worker-bulk
docker compose -f docker-compose.prod.yml exec -T postgres dropdb -U alinea alinea --force
docker compose -f docker-compose.prod.yml exec -T postgres createdb -U alinea alinea
docker compose -f docker-compose.prod.yml exec -T postgres pg_restore -U alinea -d alinea < /tmp/restore.dump
docker compose -f docker-compose.prod.yml start api worker-interactive worker-bulk
```

リストア後に取り込み途中だったジョブを回復: `docker compose ... run --rm api python -m alinea_core.jobs.requeue`。

## 7. 監視・アラート(plans/01 §9.4)

- Grafana(`grafana.alinea.app`)に Prometheus データソースを接続し、p50/p95 ダッシュボードを作成(`http_request_duration_seconds` / `job_duration_seconds` / `chat_first_token_seconds` / `llm_cost_usd_total` ほか §9.4 の表)。
- Grafana Alerting → メール: p95 目標の 2 倍超 15 分継続 / `job_queue_depth > 100` 10 分継続 / 5xx 率 > 1%。
- worker のメトリクスポート: worker-interactive=9101、worker-bulk=9102、api は 8000 の `/metrics`。

## 8. ブラウザ拡張のストア申請(Chrome Web Store / Edge Add-ons)

1. ビルド: `pnpm --filter @alinea/extension zip`(Chrome)/ `pnpm --filter @alinea/extension zip:edge`(Edge)。
2. **Chrome Web Store**: デベロッパーダッシュボード($5 登録)→ 新規アイテム → zip アップロード。
   - 掲載文: docs/08(拡張仕様)の説明を使用。カテゴリ: 仕事効率化。
   - スクリーンショット: ポップアップ 3 状態(保存前/保存直後/既にライブラリ)+ ビューア到達の 4 枚(1280×800)。
   - プライバシー: 収集データ=閲覧中の arXiv URL・書誌のみ、送信先=自ホストの alinea.app、`permissions: activeTab, storage` / `optional_host_permissions: arxiv.org` の用途を記載。
3. **Edge Add-ons**: パートナーセンター → 同 zip(zip:edge)を提出。掲載情報は Chrome と同一。
4. 審査通過後、**両ストアの拡張 ID** を `EXTENSION_ALLOWED_ORIGINS` に設定して api を再デプロイ(§5)。
5. 審査待ちはクリティカルパス(plans/13 §6.1)。リジェクト時は指摘に対処し再提出。

## 9. 初回公開チェックリスト

- [ ] `https://alinea.app` でログイン(Google / GitHub / メールリンク)ができる
- [ ] 拡張(unpacked または ストア版)から arXiv 論文を保存 → 1 分以内に readable
- [ ] `GET /api/readyz` が green(PG・Redis・R2 疎通)
- [ ] Grafana ダッシュボードにメトリクスが流れている
- [ ] Sentry にテストイベントが届く(`sentry-cli send-event`)
- [ ] バックアップ cron が初回実行され R2 にダンプが存在する
- [ ] UptimeRobot の監視が green
- [ ] `.env.production` の全キーが本番値(dev 値の残存なし。特に `SESSION_SECRET` / `ALINEA_KEY_ENCRYPTION_SECRET`)
