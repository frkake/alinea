"use client";

import { useState, type CSSProperties, type FormEvent } from "react";
import { AlineaLogo } from "@/components/brand/AlineaLogo";
import { Card } from "@/components/ui/Card";

/**
 * ログイン画面(plans/01 §6.1)。Google / GitHub OAuth + メールリンク(マジックリンク)。
 * パスワードは持たない。認証成立後 API は `/` へ 302 し、`/` が `/library` へ振り分ける。
 */

const OAUTH_PROVIDERS: Array<{ id: "google" | "github"; label: string }> = [
  { id: "google", label: "Google で続ける" },
  { id: "github", label: "GitHub で続ける" },
];

const oauthButtonStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 8,
  height: 40,
  width: "100%",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 8,
  background: "var(--pr-bg-control)",
  color: "var(--pr-text-mid)",
  fontSize: 13,
  fontWeight: 600,
  textDecoration: "none",
  cursor: "pointer",
};

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!email) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/email/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error("request failed");
      setSent(true);
    } catch {
      setError("送信に失敗しました。時間をおいて再度お試しください。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card style={{ width: 380, padding: "28px 28px 24px" }}>
      <div style={{ marginBottom: 6 }}>
        <AlineaLogo markSize={24} wordSize={16} centered />
      </div>
      <p
        style={{
          textAlign: "center",
          fontSize: 12,
          color: "var(--pr-text-sub)",
          margin: "0 0 20px",
        }}
      >
        英語論文を日本語で深く読むためのワークベンチ
      </p>

      {/* OAuth */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {OAUTH_PROVIDERS.map((p) => (
          <a key={p.id} href={`/api/auth/oauth/${p.id}/start`} style={oauthButtonStyle}>
            {p.label}
          </a>
        ))}
      </div>

      {/* 区切り */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          margin: "18px 0",
          color: "var(--pr-text-muted)",
          fontSize: 11,
        }}
      >
        <span style={{ flex: 1, height: 1, background: "var(--pr-border-hair)" }} />
        または
        <span style={{ flex: 1, height: 1, background: "var(--pr-border-hair)" }} />
      </div>

      {/* メールリンク */}
      {sent ? (
        <p
          role="status"
          style={{ fontSize: 12.5, color: "var(--pr-text-body)", lineHeight: 1.7, margin: 0 }}
        >
          <strong>{email}</strong> にログインリンクを送信しました。メールを確認してください（リンクの有効期限は 15 分です）。
        </p>
      ) : (
        <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label
            htmlFor="login-email"
            style={{ fontSize: 11.5, color: "var(--pr-text-sub)" }}
          >
            メールアドレス
          </label>
          <input
            id="login-email"
            type="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
            placeholder="you@example.com"
            style={{
              height: 40,
              padding: "0 12px",
              borderRadius: 8,
              border: "1px solid var(--pr-border-control)",
              background: "var(--pr-bg-inset)",
              color: "var(--pr-text)",
              fontSize: 13,
              fontFamily: "inherit",
            }}
          />
          {error ? (
            <span style={{ fontSize: 11.5, color: "var(--pr-warn)" }}>{error}</span>
          ) : null}
          <button
            type="submit"
            disabled={submitting}
            style={{
              height: 40,
              borderRadius: 8,
              border: "none",
              background: "var(--pr-acc)",
              color: "#FFFFFF",
              fontSize: 13,
              fontWeight: 600,
              cursor: submitting ? "default" : "pointer",
              opacity: submitting ? 0.6 : 1,
              fontFamily: "inherit",
            }}
          >
            ログインリンクを送信
          </button>
        </form>
      )}
    </Card>
  );
}
