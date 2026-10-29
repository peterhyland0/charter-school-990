import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import SiteNav from "./site-nav";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Charter school lookup",
  description: "Who runs each charter school, from IRS Form 990 filings.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-page font-sans text-ink">
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-4 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold whitespace-nowrap">
              <span className="flex size-6 items-center justify-center rounded-sm bg-accent text-white">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="size-3.5" aria-hidden="true">
                  <path d="M2 6 8 2.5 14 6M3.5 6.5v6M6.5 6.5v6M9.5 6.5v6M12.5 6.5v6M2 13.5h12" />
                </svg>
              </span>
              Charter school lookup
            </Link>
            <SiteNav />
          </div>
        </header>
        <div className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</div>
        <footer className="border-t border-line bg-surface">
          <p className="mx-auto w-full max-w-5xl px-4 py-4 text-xs text-muted">
            Sources: IRS Form 990 e-filings and the NCES Common Core of Data. Each school&apos;s figures link to the return they came from.
          </p>
        </footer>
      </body>
    </html>
  );
}
