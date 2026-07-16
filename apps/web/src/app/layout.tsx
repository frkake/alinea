import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import { ServiceWorkerRegistration } from "@/components/pwa/ServiceWorkerRegistration";
import "@/styles/globals.css";
import {
  ACCENT_COOKIE,
  DEFAULT_ACCENT,
  DEFAULT_BODY_FONT,
  DEFAULT_THEME,
  FONT_COOKIE,
  isAccentKey,
  isBodyFont,
  isThemePref,
  resolveThemeForSSR,
  THEME_COOKIE,
} from "@/lib/theme";

export const metadata: Metadata = {
  title: "Alinea",
  description: "英語論文を日本語で深く読むための研究者向けワークベンチ。",
  // PWA(S13 / M3): マニフェストは app/manifest.ts が /manifest.webmanifest として
  // 自動リンクする。ここでは iOS ホーム画面用の apple-touch-icon のみ明示する。
  appleWebApp: { capable: true, title: "Alinea", statusBarStyle: "default" },
  icons: {
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  width: 1440,
  themeColor: "#FBFAF7",
};

/**
 * FOUC 防止(plans/08 §8.2): <head> 先頭で Cookie を読み、system をその場で解決して
 * <html> の 3 属性を確定させる。SSR は system→light を初期値とするため、
 * ここでダーク解決される場合に備え <html> に suppressHydrationWarning を付ける。
 */
const THEME_INIT_SCRIPT = `(function(){try{var c=document.cookie;function g(n){var m=c.match(new RegExp('(?:^|; )'+n+'=([^;]*)'));return m?decodeURIComponent(m[1]):null;}var t=g('${THEME_COOKIE}')||'${DEFAULT_THEME}';var a=g('${ACCENT_COOKIE}')||'${DEFAULT_ACCENT}';var f=g('${FONT_COOKIE}')||'${DEFAULT_BODY_FONT}';var mq=window.matchMedia('(prefers-color-scheme: dark)');var r=document.documentElement;var resolve=function(){return t==='dark'?'dark':(t==='light'?'light':(mq.matches?'dark':'light'));};r.setAttribute('data-theme',resolve());r.setAttribute('data-accent',a);r.setAttribute('data-body-font',f);if(t==='system'&&mq.addEventListener){mq.addEventListener('change',function(){r.setAttribute('data-theme',resolve());});}}catch(e){}})();`;

export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}): Promise<ReactNode> {
  const cookieStore = await cookies();
  const themeRaw = cookieStore.get(THEME_COOKIE)?.value;
  const accentRaw = cookieStore.get(ACCENT_COOKIE)?.value;
  const fontRaw = cookieStore.get(FONT_COOKIE)?.value;

  const theme = isThemePref(themeRaw) ? themeRaw : DEFAULT_THEME;
  const accent = isAccentKey(accentRaw) ? accentRaw : DEFAULT_ACCENT;
  const bodyFont = isBodyFont(fontRaw) ? fontRaw : DEFAULT_BODY_FONT;
  const resolvedTheme = resolveThemeForSSR(theme);

  return (
    <html
      lang="ja"
      data-theme={resolvedTheme}
      data-accent={accent}
      data-body-font={bodyFont}
      suppressHydrationWarning
    >
      <head>
        {/* フォントは Google Fonts の <link> で読む(plans/08 §3.1 決定: next/font は使わない)。 */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+JP:wght@400;500;600;700&family=Noto+Serif+JP:wght@400;500;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400;1,8..60,600&family=IBM+Plex+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <ServiceWorkerRegistration />
        <Providers theme={theme} accent={accent} bodyFont={bodyFont}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
